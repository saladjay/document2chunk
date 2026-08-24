"""DocxExtractor —— .docx → ExtractionResult（lxml 直读 + 统一 postprocess）。"""

from __future__ import annotations

import io
import logging
from collections import Counter
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from document2chunk.exceptions import Document2ChunkError
from document2chunk.extractors.docx.package_reader import PackageReader
from document2chunk.extractors.docx.parser import DocumentParser
from document2chunk.extractors.docx import notes
from document2chunk.extractors.docx.styles import StyleRegistry
from document2chunk.ir import (
    BlockNode,
    DocumentMetadata,
    ExtractionResult,
    ImageNode,
    ListNode,
    ParagraphNode,
    SourceType,
    TableNode,
)

_logger = logging.getLogger(__name__)

# 图片合成：EMU → 像素转换系数 (1 inch = 914400 EMU)
_EMU_PER_INCH = 914400
# 合成组内最大段落间距（背景→前景，覆盖整页截图+多段文字场景）
_MAX_GROUP_PARA_GAP = 80
# 前景标注面积占比上限（超过则不作为标注合成）
_OVERLAY_AREA_RATIO = 0.5


class InvalidDocxError(Document2ChunkError):
    """无效的 .docx 文件（Document2ChunkError → /parse-json 422 handler 接住）。"""


def _body_font_size(blocks: List[BlockNode]) -> Optional[float]:
    """正文基准字号：全部段落 run 字号（pt）的众数（仿 BodyAnalysisStage）。

    遍历顶层段落 + 列表项内段落；表格内文字不参与（表内字号常异于正文）。
    """
    sizes: List[float] = []

    def walk(bs: List[BlockNode]) -> None:
        for b in bs:
            if isinstance(b, ParagraphNode):
                for r in b.runs or []:
                    fs = getattr(getattr(r, "style", None), "font_size", None)
                    if fs:
                        sizes.append(round(float(fs), 1))
            elif isinstance(b, ListNode):
                for item in b.items:
                    walk(item.blocks)

    walk(blocks)
    return Counter(sizes).most_common(1)[0][0] if sizes else None


def _iter_images(blocks) -> Iterator[ImageNode]:
    """递归收集块序列中的 ImageNode（含表格/列表嵌套）。"""
    for b in blocks:
        if isinstance(b, ImageNode):
            yield b
        elif isinstance(b, TableNode):
            for row in b.rows:
                for cell in row.cells:
                    yield from _iter_images(cell.blocks)
        elif isinstance(b, ListNode):
            for item in b.items:
                yield from _iter_images(item.blocks)


def _export_media(reader: PackageReader, blocks, image_dir) -> None:
    """仅落盘被引用媒体，zip 内原名；失败 WARN 跳过（对齐 PDF 路，阶段B §4.7）。

    落盘成功后把 image_id 回写为媒体文件名——serve 层加 images/ 前缀后即
    markdown 引用路径，引用与落盘对得上（issues6 #6）。回写会切断后续同
    rId 的 rels 查询链，用 resolved 缓存续查；查不到/落盘失败保持原 id。
    """
    out = Path(image_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _logger.warning("docx 媒体目录创建失败 %s: %s", out, e)
        return
    resolved: dict = {}  # rId → 落盘文件名
    for img in _iter_images(blocks):
        if not img.image_id:
            continue
        if img.image_id in resolved:
            img.image_id = resolved[img.image_id]
            continue
        info = reader.media_info_for_rel(img.image_id)
        if info is None:
            continue
        name, data, _ext = info
        try:
            (out / name).write_bytes(data)
        except OSError as e:
            _logger.warning("docx 媒体落盘失败 %s: %s", name, e)
            continue
        resolved[img.image_id] = name
        img.image_id = name


def _group_overlapping_images(
    blocks: List[BlockNode],
) -> List[Tuple[ImageNode, List[ImageNode], List[int]]]:
    """识别重叠图片组：背景截图 + 前景标注。

    返回 [(base_image, [overlay_images], [overlay_block_indices])] 列表。
    算法：每个前景标注关联其前方最近的背景截图，形成组。

    仅收集**顶层** ImageNode：表格/列表内嵌的锚定图保守不参与——嵌套索引
    与顶层移除索引空间不一致，参与会在移除时误删无关顶层块。
    """
    # 收集顶层顺序的 ImageNode 及其位置
    ordered: List[Tuple[ImageNode, int]] = [
        (b, i) for i, b in enumerate(blocks)
        if isinstance(b, ImageNode) and b.anchor_behind_doc is not None
    ]

    if not ordered:
        return []

    # 每个前景图关联其前方最近的背景图
    groups_map: dict = {}  # base_id -> (base_img, [overlays], [indices])
    last_base: Optional[ImageNode] = None
    last_base_idx: int = -1

    for img, idx in ordered:
        if img.anchor_behind_doc is True:
            last_base = img
            last_base_idx = idx
            if img.id not in groups_map:
                groups_map[img.id] = (img, [], [])
        elif img.anchor_behind_doc is False and last_base is not None:
            # 检查段落间距
            if idx - last_base_idx > _MAX_GROUP_PARA_GAP:
                continue
            # 检查面积比
            if (
                last_base.width_emu
                and last_base.height_emu
                and img.width_emu
                and img.height_emu
            ):
                base_area = last_base.width_emu * last_base.height_emu
                overlay_area = img.width_emu * img.height_emu
                if base_area > 0 and overlay_area / base_area >= _OVERLAY_AREA_RATIO:
                    continue  # 面积过大，不作为标注
            grp = groups_map[last_base.id]
            grp[1].append(img)
            grp[2].append(idx)

    # 只返回有前景标注的组
    return [(base, ovs, idxs) for base, ovs, idxs in groups_map.values() if ovs]


def _composite_overlapping_images(
    reader: PackageReader,
    blocks: List[BlockNode],
    image_dir: Optional[str],
) -> List[BlockNode]:
    """合成重叠的锚定图片：背景截图 + 前景标注 → 单张合成图。

    对每组重叠图片：
    1. 加载背景图原始像素
    2. 将前景标注按相对位置贴入背景
    3. 保存合成图并替换原背景 ImageNode
    4. 从块序列中移除前景 ImageNode
    """
    try:
        from PIL import Image as PILImage
    except ImportError:
        _logger.debug("Pillow 未安装，跳过图片合成")
        return blocks

    groups = _group_overlapping_images(blocks)
    if not groups:
        return blocks

    out_dir = Path(image_dir) if image_dir else None
    remove_ids: set = set()

    for base, overlays, overlay_indices in groups:
        # 加载背景图原始数据
        base_info = reader.media_info_for_rel(base.image_id)
        if base_info is None:
            continue
        base_name, base_data, base_ext = base_info

        try:
            bg_img = PILImage.open(io.BytesIO(base_data))
            if bg_img.mode != "RGBA":
                bg_img = bg_img.convert("RGBA")
        except Exception as e:
            _logger.warning("背景图加载失败 (%s): %s", base_name, e)
            continue

        bg_w, bg_h = bg_img.size
        base_width_emu = base.width_emu or _EMU_PER_INCH
        base_height_emu = base.height_emu or _EMU_PER_INCH
        # EMU → 像素缩放
        scale_x = bg_w / base_width_emu
        scale_y = bg_h / base_height_emu
        base_pos_h = base.anchor_pos_h_emu or 0

        composited = False
        for ov in overlays:
            ov_info = reader.media_info_for_rel(ov.image_id)
            if ov_info is None:
                continue
            _, ov_data, ov_ext = ov_info

            try:
                fg_img = PILImage.open(io.BytesIO(ov_data))
                if fg_img.mode != "RGBA":
                    fg_img = fg_img.convert("RGBA")
            except Exception as e:
                _logger.warning("前景图加载失败 (%s): %s", ov.image_id, e)
                continue

            # 计算前景在背景上的像素位置
            fg_w_emu = ov.width_emu or 0
            fg_h_emu = ov.height_emu or 0

            # 缩放前景到与背景相同的比例
            fg_pixel_w = max(1, int(fg_w_emu * scale_x))
            fg_pixel_h = max(1, int(fg_h_emu * scale_y))
            fg_resized = fg_img.resize((fg_pixel_w, fg_pixel_h), PILImage.LANCZOS)

            # 水平位置：基于 column 偏移差
            fg_pos_h = ov.anchor_pos_h_emu or 0
            offset_x = int((fg_pos_h - base_pos_h) * scale_x)
            offset_x = max(0, min(offset_x, bg_w - fg_pixel_w))

            # 垂直位置：使用段落内偏移，限制在背景范围内
            fg_pos_v = ov.anchor_pos_v_emu or 0
            offset_y = int(fg_pos_v * scale_y)
            offset_y = max(0, min(offset_y, bg_h - fg_pixel_h))

            bg_img.paste(fg_resized, (offset_x, offset_y), fg_resized)
            composited = True

        if not composited:
            continue

        # 保存合成图
        composite_name = base_name.rsplit(".", 1)[0] + "_composite.png"
        composite_data = io.BytesIO()
        bg_img.save(composite_data, format="PNG")
        composite_bytes = composite_data.getvalue()

        if out_dir is not None:
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / composite_name).write_bytes(composite_bytes)
            except OSError as e:
                _logger.warning("合成图保存失败 %s: %s", composite_name, e)
                continue

        # 更新 base ImageNode
        base.image_id = composite_name
        base.format = "png"
        base.data = composite_bytes
        base.metadata["composited"] = True
        base.metadata["overlay_count"] = len(overlays)

        # 标记前景图待移除
        remove_ids.update(overlay_indices)

        _logger.info(
            "图片合成: %s + %d 张标注 → %s",
            base_name, len(overlays), composite_name,
        )

    # 移除前景 ImageNode 块
    if remove_ids:
        blocks = [b for idx, b in enumerate(blocks) if idx not in remove_ids]

    return blocks


class DocxExtractor:
    """可编辑 .docx 提取器。"""

    source_type: SourceType = SourceType.DOCX

    def extract(
        self,
        source,
        *,
        options=None,
        heuristic_headings: bool = False,
        image_dir=None,
    ) -> ExtractionResult:
        reader = PackageReader(source)

        doc_elem = reader.document_element()
        if doc_elem is None:
            raise InvalidDocxError("缺少 word/document.xml，不是有效的 .docx")

        registry = StyleRegistry()
        registry.load(reader.styles_element())

        parser = DocumentParser(
            registry,
            numbering_elem=reader.numbering_element(),
            reader=reader,
            heuristic_headings=heuristic_headings,
        )
        blocks, toc_entries = parser.parse(doc_elem)

        # 图片合成：重叠锚定图片（背景截图 + 前景标注）合成（需在媒体落盘前）
        if image_dir is not None:
            blocks = _composite_overlapping_images(reader, blocks, image_dir)

        # 媒体落盘（仅被引用媒体；IR 不引磁盘路径）
        if image_dir is not None:
            _export_media(reader, blocks, image_dir)

        core = reader.core_properties()
        source_file = Path(source).name if isinstance(source, (str, Path)) else None

        def _meta(custom: Optional[dict] = None) -> DocumentMetadata:
            return DocumentMetadata(
                source_type=SourceType.DOCX,
                source_file=source_file,
                title=core.get("title"),
                author=core.get("author"),
                created=core.get("created"),
                modified=core.get("modified"),
                custom=custom or {},
            )

        metadata = _meta()

        # 页眉文本 → metadata.custom（不进正文，阶段B §4.8）
        header_lines = [
            " ".join("".join(h.itertext()).split()) for h in reader.header_elements()
        ]
        header_text = " / ".join(x for x in header_lines if x)[:200]
        if header_text:
            metadata.custom["docx"] = {"header_text": header_text}

        # 统一后处理（第三路汇合，designs/009）：DOCX 无页几何，页相关步骤天然 no-op；
        # 收益是 calibrate_levels（doc_title 字号比 + 栈式定级）与 split_attachments。
        from document2chunk.postprocess import postprocess
        pp_log: list = []
        main_content, attach_segments = postprocess(
            blocks, metadata,
            toc_entries=toc_entries if toc_entries else None,
            page_geometry=None,
            use_height_fallback=False,
            body_font_size=_body_font_size(blocks),
            _log=pp_log,
        )

        # 尾注/脚注内容：正文末尾集中。必须在 postprocess 之后——
        # 若在之前追加，split_attachments 会把尾注划进文档末尾的附件段（设计 §4.5）
        main_content = (
            main_content
            + notes.parse_notes(reader, "endnote")
            + notes.parse_notes(reader, "footnote")
        )

        result = ExtractionResult(
            content=main_content,
            metadata=metadata,
            toc_entries=toc_entries if toc_entries else None,
        )
        for seg in attach_segments:
            result.attachments.append(ExtractionResult(content=seg, metadata=_meta(
                custom={"is_attachment": True})))

        debug_dir = None
        if isinstance(options, dict):
            debug_dir = options.get("debug_dir")
        else:
            debug_dir = getattr(options, "debug_dir", None)
        if debug_dir:
            import json as _json
            import os as _os
            _os.makedirs(str(debug_dir), exist_ok=True)
            with open(_os.path.join(str(debug_dir), "postprocess_log.json"), "w", encoding="utf-8") as f:
                _json.dump(pp_log, f, ensure_ascii=False, indent=2)
        return result
