"""图片检测 Stage。

检测 PDF 中的图片区域，将**真图片**区域内的文本替换为占位符元素；
**背景/装饰图**（全页底纹、带文字叠层）不替换、保留文字（designs/004）。

三信号分层判定每张图的真实类型（可靠度从高到低）：
1. **span 文字存在性**（主裁判，零依赖）：图 bbox 内有显著可编辑文字 → 文字叠层/背景 → 保留文字。
2. **page_coverage**（复用 pdf_detect 口径）：图占页面 > 阈值且无文字 → 全页背景。
3. **layout label**（可选，方向性包含非 IOU）：有 layout_data 时裁决 span 判不定的纯图。

位置：在 BodyAnalysis 之后执行。
"""

from __future__ import annotations

from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.stages.layout_filter import layout_boxes_for_page

# ---- 常量（designs/004 §6）----
COVERAGE_BG = 0.5  # 图占页面积 > 此 → 疑似全页背景（同 pdf_detect.LARGE_IMAGE_AREA_RATIO）
TEXT_IN_IMG_MIN = 3  # 图内可编辑文字元素 ≥ 此 → 文字叠层（保留文字）
LAYOUT_CONTAIN_MIN = 0.5  # 方向性 contain ≥ 此 → 视为该版面区域

# 非图区 label（版面说这里是文字/表/页眉页脚 → 不出 figure 占位符）
_NON_FIGURE_LABELS = {
    "text", "title", "header", "footer", "page_header", "page_footer",
    "number", "page_number", "table",
}
_FIGURE_LABELS = {"figure", "image", "picture"}


# ============================================================
# 几何工具
# ============================================================


def _bbox_area(b: list[float]) -> float:
    if not b or len(b) < 4:
        return 0.0
    return max(0.0, (b[2] - b[0])) * max(0.0, (b[3] - b[1]))


def _intersection_area(a: list[float], b: list[float]) -> float:
    if len(a) < 4 or len(b) < 4:
        return 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix0 >= ix1 or iy0 >= iy1:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def _center_in(px: float, py: float, b: list[float]) -> bool:
    return len(b) >= 4 and b[0] <= px <= b[2] and b[1] <= py <= b[3]


def _best_layout_overlap(
    img_bbox: list[float], layout_boxes: list[tuple[str, list[float]]]
) -> tuple[str | None, float]:
    """与图交面积最大的版面 box → (label, contain=交/layoutbox面积)。方向性，非 IOU。"""
    best_label: str | None = None
    best_contain = 0.0
    for label, lbox in layout_boxes:
        inter = _intersection_area(img_bbox, lbox)
        larea = _bbox_area(lbox)
        if inter <= 0 or larea <= 0:
            continue
        contain = inter / larea
        if contain > best_contain:
            best_contain = contain
            best_label = label
    return best_label, best_contain


# ============================================================
# Stage
# ============================================================


class ImageDetectionStage:
    """图片检测与占位符替换（designs/004 三信号融合）。

    - is_global = False（逐页）
    - 从 ctx.image_infos 取图，按 _classify_image 过滤出**真 figure**
    - 仅真 figure 替换其内文字为占位符；背景/装饰图保留文字
    """

    @property
    def name(self) -> str:
        return "image_detection"

    @property
    def is_global(self) -> bool:
        return False

    def process(self, elements: list[dict], ctx: PipelineContext) -> list[dict]:
        image_infos = getattr(ctx, "image_infos", None) or []
        if not image_infos:
            return elements

        page_w = getattr(ctx, "page_width", 0.0) or 0.0
        page_h = getattr(ctx, "page_height", 0.0) or 0.0
        layout_boxes = layout_boxes_for_page(
            getattr(ctx, "layout_data", None), getattr(ctx, "page_index", 0), page_w, page_h
        )

        # 分类：只保留真 figure（background/skip 的图忽略，文字保留）
        figure_images: list[dict] = []
        for img in image_infos:
            bbox = img.get("bbox", [])
            if len(bbox) < 4:
                continue
            if self._classify_image(bbox, elements, page_w, page_h, layout_boxes) == "figure":
                figure_images.append(
                    {
                        "x0": bbox[0], "y0": bbox[1], "x1": bbox[2], "y1": bbox[3],
                        "filename": img.get("filename", ""),
                        "image_id": img.get("image_id", ""),
                    }
                )

        if not figure_images:
            return elements  # 无真 figure，文字全保留

        # 现行替换逻辑（仅作用于 figure_images）
        result_elements: list[dict] = []
        placeholder_count = 0
        used_image_ids: set[str] = set()

        for elem in elements:
            bbox = elem.get("bbox", [])
            if len(bbox) < 4:
                result_elements.append(elem)
                continue

            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            matched = None
            for img_bbox in figure_images:
                # 中心点在图内
                if _center_in(cx, cy, [img_bbox["x0"], img_bbox["y0"], img_bbox["x1"], img_bbox["y1"]]):
                    matched = img_bbox
                    break
                # 或交集占文本面积 > 50%
                if _intersection_area(bbox, [img_bbox["x0"], img_bbox["y0"], img_bbox["x1"], img_bbox["y1"]]) / max(_bbox_area(bbox), 1e-9) > 0.5:
                    matched = img_bbox
                    break

            if matched:
                image_id = matched["image_id"]
                if image_id not in used_image_ids:
                    result_elements.append(self._placeholder(matched, elem.get("order_index", 0)))
                    used_image_ids.add(image_id)
                    placeholder_count += 1
                # 跳过图内文本
            else:
                result_elements.append(elem)

        # Fallback：未匹配到文本的 figure 仍出占位符（放末尾）
        for img_bbox in figure_images:
            if img_bbox["image_id"] not in used_image_ids:
                result_elements.append(self._placeholder(img_bbox, 9999))
                used_image_ids.add(img_bbox["image_id"])
                placeholder_count += 1

        if placeholder_count > 0:
            ctx.stats["image_placeholders"] = placeholder_count

        return self._merge_overlapping_images(result_elements)

    @staticmethod
    def _placeholder(img_bbox: dict, order_index) -> dict:
        return {
            "type": "image",
            "text": img_bbox.get("image_id", ""),
            "bbox": [round(v, 2) for v in (img_bbox["x0"], img_bbox["y0"], img_bbox["x1"], img_bbox["y1"])],
            "order_index": order_index,
        }

    @staticmethod
    def _classify_image(
        img_bbox: list[float],
        elements: list[dict],
        page_w: float,
        page_h: float,
        layout_boxes: list[tuple[str, list[float]]],
    ) -> str:
        """判定图片真实类型：'figure' | 'background' | 'skip'（designs/004 §3.1）。"""
        img_area = _bbox_area(img_bbox)
        page_area = page_w * page_h

        # 图内可编辑文字（中心点落入图 bbox）
        text_inside = 0
        for e in elements:
            eb = e.get("bbox", [])
            if len(eb) < 4:
                continue
            if (e.get("text") or "").strip() and _center_in((eb[0] + eb[2]) / 2, (eb[1] + eb[3]) / 2, img_bbox):
                text_inside += 1

        # ① span 主裁判（editable ground truth）：图内有显著文字 → 背景/文字叠层
        if text_inside >= TEXT_IN_IMG_MIN:
            return "background"

        # ② page_coverage：全页背景且无文字
        coverage = img_area / page_area if page_area > 0 else 0.0
        if coverage > COVERAGE_BG and text_inside == 0:
            return "background"

        # ③ layout 裁决（span 判不了的纯图）
        if layout_boxes:
            label, contain = _best_layout_overlap(img_bbox, layout_boxes)
            if label in _FIGURE_LABELS and contain >= LAYOUT_CONTAIN_MIN:
                return "figure"
            if label in _NON_FIGURE_LABELS:
                return "skip"

        # ④ 默认：小图、无文字、无/含糊 layout → 真 figure
        return "figure"

    # ---------- 占位符合并（沿用原逻辑）----------

    @staticmethod
    def _bboxes_intersect(bbox1: list[float], bbox2: list[float]) -> bool:
        if len(bbox1) < 4 or len(bbox2) < 4:
            return False
        return not (
            bbox1[2] <= bbox2[0] or bbox1[0] >= bbox2[2]
            or bbox1[3] <= bbox2[1] or bbox1[1] >= bbox2[3]
        )

    @staticmethod
    def _merge_bboxes(bbox1: list[float], bbox2: list[float]) -> list[float]:
        if len(bbox1) < 4 or len(bbox2) < 4:
            return bbox1 if len(bbox1) >= 4 else bbox2
        return [min(bbox1[0], bbox2[0]), min(bbox1[1], bbox2[1]), max(bbox1[2], bbox2[2]), max(bbox1[3], bbox2[3])]

    def _merge_overlapping_images(self, elements: list[dict]) -> list[dict]:
        image_elements = [e for e in elements if e.get("type") == "image"]
        other_elements = [e for e in elements if e.get("type") != "image"]
        if len(image_elements) <= 1:
            return elements

        n = len(image_elements)
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for i in range(n):
            for j in range(i + 1, n):
                if self._bboxes_intersect(image_elements[i].get("bbox", []), image_elements[j].get("bbox", [])):
                    union(i, j)

        groups: dict[int, list[dict]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(image_elements[i])

        merged = []
        for group in groups.values():
            merged.append(group[0] if len(group) == 1 else self._merge_image_group(group))

        all_elements = other_elements + merged
        all_elements.sort(key=lambda e: e.get("order_index", 0))
        return all_elements

    @staticmethod
    def _merge_image_group(images: list[dict]) -> dict:
        if not images:
            raise ValueError("Cannot merge empty image group")
        first = images[0]
        merged_bbox = first.get("bbox", [0, 0, 0, 0])[:4]
        min_order = first.get("order_index", 0)
        for img in images[1:]:
            merged_bbox = ImageDetectionStage._merge_bboxes(merged_bbox, img.get("bbox", [0, 0, 0, 0]))
            order = img.get("order_index", 0)
            if order < min_order:
                min_order = order
        return {
            "type": "image",
            "text": first.get("text", ""),
            "bbox": [round(v, 2) for v in merged_bbox],
            "order_index": min_order,
        }
