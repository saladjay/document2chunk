"""图片检测 Stage。

检测 PDF 中的图片区域，将图片区域内的文本替换为占位符元素。
图片已从 PDF 中提取并保存为独立文件，占位符包含图片引用信息。

位置：在 BodyAnalysis 之后执行，用于处理截图中的文字。
"""

from __future__ import annotations

from document2chunk.pipeline.base import PipelineContext


class ImageDetectionStage:
    """图片检测与占位符替换。

    - is_global = False（每页独立处理）
    - 从 ctx.image_infos 获取已提取的图片信息（由提取前端预提取）
    - 检查文本元素是否与图片 bbox 有交集
    - 如果文本在图片内，替换为图片占位符元素

    判断规则：
    - 文本 bbox 中心点在图片 bbox 内 → 属于图片
    - 或文本 bbox 与图片 bbox 交集面积 > 50% 文本面积 → 属于图片

    占位符元素格式（极简）：
    - type = "image"
    - text = image_id（如 "p3_1" 表示第3页第1张图）
    - bbox = 图片的边界框
    """

    @property
    def name(self) -> str:
        return "image_detection"

    @property
    def is_global(self) -> bool:
        return False

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        # 获取当前页的图片信息
        image_infos = getattr(ctx, "image_infos", None) or []

        if not image_infos:
            return elements

        # 构建图片 bbox 列表：[{bbox, filename, image_id}, ...]
        image_bboxes = []
        for img in image_infos:
            bbox = img.get("bbox", [])
            if len(bbox) >= 4:
                image_bboxes.append(
                    {
                        "x0": bbox[0],
                        "y0": bbox[1],
                        "x1": bbox[2],
                        "y1": bbox[3],
                        "filename": img.get("filename", ""),
                        "image_id": img.get("image_id", ""),
                    }
                )

        if not image_bboxes:
            return elements

        # 处理元素：将图片区域内的文本替换为占位符
        result_elements = []
        placeholder_count = 0
        used_image_ids = set()  # 避免同一张图片生成多个占位符

        for elem in elements:
            bbox = elem.get("bbox", [])

            if len(bbox) < 4:
                result_elements.append(elem)
                continue

            elem_x0, elem_y0, elem_x1, elem_y1 = bbox[:4]

            elem_center_x = (elem_x0 + elem_x1) / 2
            elem_center_y = (elem_y0 + elem_y1) / 2

            matched_image = None

            for img_bbox in image_bboxes:
                img_x0, img_y0, img_x1, img_y1 = (
                    img_bbox["x0"],
                    img_bbox["y0"],
                    img_bbox["x1"],
                    img_bbox["y1"],
                )

                # 检查中心点是否在图片内
                if (
                    img_x0 <= elem_center_x <= img_x1
                    and img_y0 <= elem_center_y <= img_y1
                ):
                    matched_image = img_bbox
                    break

                # 或者计算交集面积比例
                inter_x0 = max(elem_x0, img_x0)
                inter_y0 = max(elem_y0, img_y0)
                inter_x1 = min(elem_x1, img_x1)
                inter_y1 = min(elem_y1, img_y1)

                if inter_x0 < inter_x1 and inter_y0 < inter_y1:
                    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
                    elem_area = (elem_x1 - elem_x0) * (elem_y1 - elem_y0)

                    if elem_area > 0 and inter_area / elem_area > 0.5:
                        matched_image = img_bbox
                        break

            if matched_image:
                image_id = matched_image["image_id"]
                # 同一张图片只生成一个占位符
                if image_id not in used_image_ids:
                    img_bbox = [
                        matched_image["x0"],
                        matched_image["y0"],
                        matched_image["x1"],
                        matched_image["y1"],
                    ]
                    placeholder = {
                        "type": "image",
                        "text": image_id,
                        "bbox": [round(v, 2) for v in img_bbox],
                        "order_index": elem.get("order_index", 0),
                    }
                    result_elements.append(placeholder)
                    used_image_ids.add(image_id)
                    placeholder_count += 1
                # 跳过图片内的文本元素
            else:
                result_elements.append(elem)

        # Fallback：对于没有匹配到文本的图片，仍生成占位符
        # 处理 layout_data 为空（扫描页/图片页）但有提取图片的情况
        for img_bbox in image_bboxes:
            image_id = img_bbox["image_id"]
            if image_id not in used_image_ids:
                img_box = [
                    img_bbox["x0"],
                    img_bbox["y0"],
                    img_bbox["x1"],
                    img_bbox["y1"],
                ]
                placeholder = {
                    "type": "image",
                    "text": image_id,
                    "bbox": [round(v, 2) for v in img_box],
                    "order_index": 9999,  # 放到末尾
                }
                result_elements.append(placeholder)
                used_image_ids.add(image_id)
                placeholder_count += 1

        if placeholder_count > 0:
            ctx.stats["image_placeholders"] = placeholder_count

        # 合并相交的图片元素
        result_elements = self._merge_overlapping_images(result_elements)

        return result_elements

    @staticmethod
    def _bboxes_intersect(bbox1: list[float], bbox2: list[float]) -> bool:
        """判断两个 bbox 是否相交（含边接触）。bbox 格式: [x0, y0, x1, y1]。"""
        if len(bbox1) < 4 or len(bbox2) < 4:
            return False

        x0_1, y0_1, x1_1, y1_1 = bbox1[:4]
        x0_2, y0_2, x1_2, y1_2 = bbox2[:4]

        return not (
            x1_1 <= x0_2  # bbox1 在 bbox2 左边
            or x0_1 >= x1_2  # bbox1 在 bbox2 右边
            or y1_1 <= y0_2  # bbox1 在 bbox2 上边
            or y0_1 >= y1_2  # bbox1 在 bbox2 下边
        )

    @staticmethod
    def _merge_bboxes(bbox1: list[float], bbox2: list[float]) -> list[float]:
        """合并两个 bbox：left/top 取最小，right/bottom 取最大。"""
        if len(bbox1) < 4 or len(bbox2) < 4:
            return bbox1 if len(bbox1) >= 4 else bbox2

        return [
            min(bbox1[0], bbox2[0]),
            min(bbox1[1], bbox2[1]),
            max(bbox1[2], bbox2[2]),
            max(bbox1[3], bbox2[3]),
        ]

    def _merge_overlapping_images(self, elements: list[dict]) -> list[dict]:
        """合并相交的图片元素（并查集分组，每组合并 bbox）。"""
        image_elements = []
        other_elements = []

        for elem in elements:
            if elem.get("type") == "image":
                image_elements.append(elem)
            else:
                other_elements.append(elem)

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
            root_x, root_y = find(x), find(y)
            if root_x != root_y:
                parent[root_x] = root_y

        for i in range(n):
            for j in range(i + 1, n):
                bbox_i = image_elements[i].get("bbox", [])
                bbox_j = image_elements[j].get("bbox", [])
                if self._bboxes_intersect(bbox_i, bbox_j):
                    union(i, j)

        groups: dict[int, list[dict]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(image_elements[i])

        merged_images = []
        for group in groups.values():
            if len(group) == 1:
                merged_images.append(group[0])
            else:
                merged_images.append(self._merge_image_group(group))

        all_elements = other_elements + merged_images
        all_elements.sort(key=lambda e: e.get("order_index", 0))
        return all_elements

    @staticmethod
    def _merge_image_group(images: list[dict]) -> dict:
        """合并一组相交的图片元素。"""
        if not images:
            raise ValueError("Cannot merge empty image group")

        first = images[0]
        merged_bbox = first.get("bbox", [0, 0, 0, 0])[:4]
        min_order = first.get("order_index", 0)

        for img in images[1:]:
            bbox = img.get("bbox", [0, 0, 0, 0])
            merged_bbox = ImageDetectionStage._merge_bboxes(merged_bbox, bbox)
            order = img.get("order_index", 0)
            if order < min_order:
                min_order = order

        return {
            "type": "image",
            "text": first.get("text", ""),
            "bbox": [round(v, 2) for v in merged_bbox],
            "order_index": min_order,
        }
