"""debug 模块 —— 管线追踪可视化（IR 可视化 + debug_dir 过程可视化）。

行为契约见 ``openspec/specs/debug/spec.md``。

公共 API：
- :func:`visualize` —— 消费 :class:`LogicalDocument`，源感知输出 bbox 叠加 / 结构树。
- :func:`visualize_debug_dir` —— 消费 session ① 的 debug_dir，复刻旧库过程可视化。
- :func:`visualize_batch` —— 批量。
"""

from __future__ import annotations

from ._annotate import (
    block_summary,
    block_to_element,
    draw_annotations,
    draw_structure_tree,
    render_structure_tree_text,
)
from ._comparison import generate_stage_comparison, load_debug_jsons
from ._render import (
    MissingPyMuPDFError,
    has_pymupdf,
    is_image,
    is_pdf,
    pdf_to_pixel,
    render_page_background,
    scale_for,
)
from ._style import FONT_PATHS, TYPE_COLORS, color_for, heading_color, load_font
from .visualize import visualize, visualize_batch, visualize_debug_dir

__all__ = [
    # 入口
    "visualize",
    "visualize_debug_dir",
    "visualize_batch",
    # 绘制实体（spec §8）
    "draw_annotations",
    "draw_structure_tree",
    "render_structure_tree_text",
    "generate_stage_comparison",
    "render_page_background",
    "block_to_element",
    "block_summary",
    "load_debug_jsons",
    # 样式 / 渲染常量
    "TYPE_COLORS",
    "FONT_PATHS",
    "color_for",
    "heading_color",
    "load_font",
    "pdf_to_pixel",
    "scale_for",
    "has_pymupdf",
    "is_pdf",
    "is_image",
    "MissingPyMuPDFError",
]
