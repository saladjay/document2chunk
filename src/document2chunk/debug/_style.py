"""可视化样式：类型配色、中文字体多平台 fallback、字号/线宽。

复刻自 ``doc-paddle-ocr/visualize_pipeline.py`` 的 ``TYPE_COLORS`` / ``FONT_PATHS``，
并扩展到 :class:`document2chunk.ir.BlockType` 全部取值（见 debug spec §5）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import ImageFont

log = logging.getLogger(__name__)

# 元素类型 → 颜色（RGB）。
#
# 同时覆盖两类取值：
#   - 规范 IR 的 ``BlockType``（heading/paragraph/table/list/image/formula/toc）
#   - 过程态 / debug_dir 旧库类型（title/toc_entry/toc_title/page_number）
#     —— 供 ``visualize_debug_dir`` 消费 session ① 的 stage JSON 时复用。
TYPE_COLORS: Dict[Optional[str], Tuple[int, int, int]] = {
    # 规范 IR BlockType
    "heading": (255, 140, 0),        # 橙
    "paragraph": (0, 100, 255),      # 蓝
    "table": (128, 0, 128),          # 紫
    "list": (0, 180, 180),           # 青
    "image": (160, 160, 160),        # 灰
    "formula": (200, 0, 160),        # 品红
    "toc": (0, 100, 0),              # 深绿
    # 过程态 / debug_dir 旧库类型（兼容 session ① 的 element dict）
    "title": (220, 30, 30),          # 红（旧库 H1）
    "toc_entry": (34, 139, 34),      # 绿
    "toc_title": (0, 100, 0),        # 深绿
    "page_number": (200, 180, 0),    # 黄（过程态）
    None: (200, 200, 200),           # 浅灰 fallback
}

# 标题按 level 的渐变端点（L1 深橙 → L9 浅橙），可选增强。
_HEADING_LEVEL_LOW = (200, 70, 0)      # L1 深橙
_HEADING_LEVEL_HIGH = (255, 200, 120)  # L9 浅橙

# 线宽：标题类用粗线（复刻旧库）。
TYPE_LINE_WIDTH: Dict[Optional[str], int] = {
    "title": 3,
    "heading": 3,
    None: 1,
}
DEFAULT_LINE_WIDTH = 2

# 中文字体路径（多平台 fallback，复刻旧库 FONT_PATHS）。
FONT_PATHS = [
    r"C:\Windows\Fonts\msyh.ttc",       # Windows 微软雅黑
    r"C:\Windows\Fonts\msyhbd.ttc",     # Windows 微软雅黑粗体
    r"C:\Windows\Fonts\simsun.ttc",     # Windows 宋体
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
    "/System/Library/Fonts/PingFang.ttc",                       # macOS
]

# 标注字号（像素）。
ANNOTATION_FONT_SIZE = 11
STATS_FONT_SIZE = 12
HEADER_FONT_SIZE = 15

_FONT_WARNED = False  # 仅对「找不到任何中文字体」警告一次


def load_font(size: int) -> ImageFont.ImageFont:
    """按优先级加载中文字体；全部缺失时降级 PIL 默认字体并 WARN 一次。"""
    global _FONT_WARNED
    for p in FONT_PATHS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001 - 字体文件损坏等，换下一个
                continue
    if not _FONT_WARNED:
        log.warning("未找到中文字体（%s），标注文字可能显示为方框", ", ".join(FONT_PATHS))
        _FONT_WARNED = True
    return ImageFont.load_default()


def color_for(etype: Optional[str]) -> Tuple[int, int, int]:
    """取元素类型对应配色，未知类型回落到浅灰。"""
    return TYPE_COLORS.get(etype, TYPE_COLORS[None])


def heading_color(level: Optional[int]) -> Tuple[int, int, int]:
    """标题按 level 渐变色（L1 深橙 → L9 浅橙）；level 缺失用基准橙。"""
    if not level or level <= 0:
        return TYPE_COLORS["heading"]
    t = (max(1, min(level, 9)) - 1) / 8.0
    return tuple(  # type: ignore[return-value]
        round(_HEADING_LEVEL_LOW[i] + (_HEADING_LEVEL_HIGH[i] - _HEADING_LEVEL_LOW[i]) * t)
        for i in range(3)
    )


def line_width_for(etype: Optional[str]) -> int:
    return TYPE_LINE_WIDTH.get(etype, DEFAULT_LINE_WIDTH)
