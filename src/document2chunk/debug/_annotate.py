"""绘制层：bbox 叠加视图（PDF/OCR）与结构树视图（docx 主用）。

两种视图共用 :func:`draw_annotations`：IR 模式把 :class:`BlockNode` 归一化成
element dict，过程模式直接消费 debug_dir 的 element dict（schema 一致）。
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from document2chunk.ir import (
    FormulaNode,
    HeadingNode,
    HyperlinkNode,
    ImageNode,
    ListNode,
    LogicalDocument,
    ParagraphNode,
    RunNode,
    TableNode,
    TocNode,
)

from ._style import (
    ANNOTATION_FONT_SIZE,
    HEADER_FONT_SIZE,
    STATS_FONT_SIZE,
    color_for,
    heading_color,
    line_width_for,
    load_font,
)

log = logging.getLogger(__name__)

_STATS_PANEL_HEIGHT = 90
_HEADER_HEIGHT = 28
_PREVIEW_LEN = 40

# 统计面板图例的类型顺序（IR 优先，过程态次之）。
_LEGEND_TYPES = [
    "heading",
    "paragraph",
    "table",
    "list",
    "image",
    "formula",
    "toc",
    "title",
    "toc_entry",
    "toc_title",
    "page_number",
]


# ---------------------------------------------------------------------------
# BlockNode → element dict（IR 模式归一化）
# ---------------------------------------------------------------------------


def _iter_runs(block: Any):
    """产出块内的 RunNode（用于提取字符样式）。"""
    if isinstance(block, HeadingNode):
        yield from block.runs
    elif isinstance(block, ParagraphNode):
        for r in block.runs:
            if isinstance(r, RunNode):
                yield r
            elif isinstance(r, HyperlinkNode):
                yield from r.runs


def block_to_element(block: Any) -> Dict[str, Any]:
    """把规范 IR 块节点归一化成 draw_annotations 消费的 element dict。

    schema: ``{type, level, bbox, confidence, style:{font,size}}``，
    与 debug_dir 过程态 element dict 兼容（过程态额外字段被忽略）。
    """
    prov = getattr(block, "provenance", None)
    style: Dict[str, Any] = {}
    for run in _iter_runs(block):
        if run.style and (run.style.font or run.style.font_size):
            style["font"] = run.style.font
            style["size"] = run.style.font_size
            break
    return {
        "type": block.type,
        "level": getattr(block, "level", None),
        "bbox": list(prov.bbox) if (prov and prov.bbox) else None,
        "confidence": getattr(prov, "confidence", None),
        "style": style,
    }


def _element_color(elem: Dict[str, Any]) -> Tuple[int, int, int]:
    etype = elem.get("type")
    if etype in ("heading", "title"):
        lvl = elem.get("level") if etype == "heading" else 1
        return heading_color(lvl)
    return color_for(etype)


def _build_label(elem: Dict[str, Any]) -> str:
    etype = elem.get("type") or "null"
    parts = [etype]
    level = elem.get("level")
    if level is not None:
        parts.append(f"L{level}")
    style = elem.get("style") or {}
    size = style.get("size")
    if size:
        parts.append(f"{size:.0f}pt")
    font_name = style.get("font")
    if font_name:
        if len(font_name) > 12:
            font_name = font_name[:10] + ".."
        parts.append(font_name)
    conf = elem.get("confidence")
    if conf is not None:
        parts.append(f"conf={conf:.2f}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# bbox 叠加视图（PDF/OCR）
# ---------------------------------------------------------------------------


def draw_annotations(
    img: Image.Image,
    elements: List[Dict[str, Any]],
    *,
    scale: float,
    header_text: str,
    body_font_info: Optional[Dict[str, Any]] = None,
) -> Image.Image:
    """在底图上绘制 bbox 框 + 标签 + 顶部 header + 底部统计面板。

    Args:
        img: 页面底图（RGB/RGBA）。
        elements: 归一化 element dict 列表（需含 ``bbox`` 才会绘制）。
        scale: 坐标→像素缩放比（PDF = dpi/72，图片 = 1.0）。
        header_text: 顶部标题（source_type + page_index [+ stage]）。
        body_font_info: ``{body_font, body_font_size}`` 或 None。
    """
    font_label = load_font(ANNOTATION_FONT_SIZE)
    font_header = load_font(HEADER_FONT_SIZE)
    font_stats = load_font(STATS_FONT_SIZE)

    page_h = img.height
    new_height = page_h + _STATS_PANEL_HEIGHT
    extended = Image.new("RGBA", (img.width, new_height), (255, 255, 255, 255))
    extended.paste(img.convert("RGBA"), (0, 0))

    overlay = Image.new("RGBA", extended.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    # 顶部标题栏底线（不填充背景，避免遮挡最上方块）—— designs/007 R3
    overlay_draw.line([(0, _HEADER_HEIGHT), (img.width, _HEADER_HEIGHT)], fill=(200, 200, 200), width=1)
    # 底部统计面板背景
    overlay_draw.rectangle([0, page_h, img.width, new_height], fill=(255, 255, 255, 240))

    composite = Image.alpha_composite(extended, overlay)
    draw = ImageDraw.Draw(composite)

    draw.text((8, 4), header_text, fill=(30, 30, 30), font=font_header)

    skipped = 0
    for elem in elements:
        bbox = elem.get("bbox")
        if not bbox or len(bbox) < 4:
            skipped += 1
            continue
        color = _element_color(elem)
        width = line_width_for(elem.get("type"))
        px0, py0, px1, py1 = _scale_bbox(bbox, scale)
        draw.rectangle([px0, py0, px1, py1], outline=color, width=width)

        label_text = _build_label(elem)
        tb = font_label.getbbox(label_text)
        text_w = tb[2] - tb[0]
        text_h = tb[3] - tb[1]
        label_y = max(0, py0 - text_h - 3)
        label_x = px0

        label_overlay = Image.new("RGBA", composite.size, (0, 0, 0, 0))
        lod = ImageDraw.Draw(label_overlay)
        lod.rectangle(
            [label_x, label_y, label_x + text_w + 4, label_y + text_h + 2],
            fill=(255, 255, 255, 190),
        )
        composite = Image.alpha_composite(composite, label_overlay)
        draw = ImageDraw.Draw(composite)
        draw.text((label_x + 2, label_y), label_text, fill=color, font=font_label)

    if skipped:
        log.debug("draw_annotations: %d 个元素缺少 bbox，已跳过", skipped)

    _draw_stats_panel(draw, elements, body_font_info, font_stats, page_h, img.width)
    return composite.convert("RGB")


def _scale_bbox(bbox: List[float], scale: float) -> Tuple[int, int, int, int]:
    return (
        int(bbox[0] * scale),
        int(bbox[1] * scale),
        int(bbox[2] * scale),
        int(bbox[3] * scale),
    )


def _draw_stats_panel(
    draw: ImageDraw.ImageDraw,
    elements: List[Dict[str, Any]],
    body_font_info: Optional[Dict[str, Any]],
    font,
    panel_y: int,
    panel_width: int,
) -> None:
    total = len(elements)
    type_counts = Counter(elem.get("type") for elem in elements)

    draw.line([(0, panel_y), (panel_width, panel_y)], fill=(180, 180, 180), width=1)

    y = panel_y + 6
    draw.text((10, y), f"统计: 共 {total} 个元素", fill=(30, 30, 30), font=font)
    y += 18

    type_parts = []
    for t, cnt in type_counts.most_common():
        type_parts.append(f"{t or 'null'}: {cnt}")
    draw.text((10, y), " | ".join(type_parts), fill=(60, 60, 60), font=font)
    y += 18

    if body_font_info:
        bf = body_font_info.get("body_font", "?")
        bs = body_font_info.get("body_font_size")
        bs_str = f"{bs:.1f}pt" if bs else "?"
        draw.text((10, y), f"正文基准: {bf}  {bs_str}", fill=(80, 80, 80), font=font)
    y += 18

    legend_x = 10
    for etype in _LEGEND_TYPES:
        if etype not in type_counts:
            continue
        color = color_for(etype)
        draw.rectangle([legend_x, y, legend_x + 10, y + 10], fill=color)
        draw.text((legend_x + 13, y - 1), etype, fill=(60, 60, 60), font=font)
        text_w = font.getbbox(etype)[2]
        legend_x += text_w + 28
        if legend_x > panel_width - 80:
            break


# ---------------------------------------------------------------------------
# 结构树视图（docx 主用，无 bbox）
# ---------------------------------------------------------------------------


def _preview(text: Optional[str]) -> str:
    if not text:
        return ""
    text = " ".join(str(text).split())
    return text[:_PREVIEW_LEN] + ("…" if len(text) > _PREVIEW_LEN else "")


def block_summary(block: Any) -> str:
    """单行块摘要（结构树视图用）。"""
    if isinstance(block, HeadingNode):
        return f"[H{block.level}] {block.text}"
    if isinstance(block, ParagraphNode):
        return f"[para] {_preview(block.text)}"
    if isinstance(block, TableNode):
        return f"[table] {len(block.rows)} 行"
    if isinstance(block, ListNode):
        kind = "有序" if block.ordered else "无序"
        return f"[list] {len(block.items)} 项（{kind}）"
    if isinstance(block, ImageNode):
        return f"[image] {block.alt or block.image_id}"
    if isinstance(block, FormulaNode):
        return f"[formula] {_preview(block.latex or block.text)}"
    if isinstance(block, TocNode):
        return f"[toc] {len(block.entries)} 条"
    return f"[{getattr(block, 'type', '?')}]"


def structure_tree_lines(doc: LogicalDocument) -> List[Tuple[int, str, Tuple[int, int, int]]]:
    """遍历 section_tree 生成 (缩进, 文本, 颜色) 行列表。"""
    lines: List[Tuple[int, str, Tuple[int, int, int]]] = []

    def walk(section, depth):
        title = section.title or "(无标题)"
        head = f"{title}   L{section.level}"
        if section.heading_node_id:
            head += f"   ← {section.heading_node_id}"
        lines.append((depth, head, (30, 30, 30)))
        for bid in section.block_ids:
            block = doc.get_block(bid)
            if block is None:
                lines.append((depth + 1, f"(missing {bid})", (160, 160, 160)))
                continue
            color = color_for(getattr(block, "type", None))
            lines.append((depth + 1, block_summary(block), color))
        for child in section.subsections:
            walk(child, depth + 1)

    walk(doc.section_tree, 0)
    return lines


def render_structure_tree_text(doc: LogicalDocument) -> str:
    """结构树纯文本（也作为 .txt 导出 / 无字体降级）。"""
    out = []
    for depth, text, _ in structure_tree_lines(doc):
        out.append("  " * depth + text)
    return "\n".join(out)


def draw_structure_tree(
    doc: LogicalDocument,
    *,
    out_path: str | Path,
    font_size: int = 14,
    header: str = "Structure Tree",
) -> Path:
    """渲染结构树为 PNG（并旁路写一份 .txt）。

    docx 主用：不渲染页面、不依赖 bbox。
    """
    out_path = Path(out_path)
    lines = structure_tree_lines(doc)
    font = load_font(font_size)

    line_h = font_size + 8
    indent_px = 22
    pad = 12
    header_h = line_h + pad

    # 量算最大行宽
    max_w = 0
    widths = []
    for depth, text, _ in lines:
        w = font.getbbox(text)[2] + depth * indent_px
        widths.append(w)
        max_w = max(max_w, w)

    img_w = max(max_w + pad * 2, 360)
    img_h = header_h + line_h * max(len(lines), 1) + pad

    img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((pad, pad // 2), header, fill=(20, 20, 20), font=load_font(font_size + 1))

    for i, (depth, text, color) in enumerate(lines):
        y = header_h + i * line_h
        x = pad + depth * indent_px
        # 缩进标记
        if depth > 0:
            draw.line([(pad, y + line_h // 2), (x - 4, y + line_h // 2)], fill=(210, 210, 210))
        draw.text((x, y), text, fill=color, font=font)

    img.save(str(out_path), "PNG")
    # 旁路文本
    txt_path = out_path.with_suffix(".txt")
    txt_path.write_text(render_structure_tree_text(doc), encoding="utf-8")
    return out_path
