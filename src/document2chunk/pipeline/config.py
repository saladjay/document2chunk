"""span 管线共享常量。

仅保留 9-Stage 与提取前端在用的常量（依据 ``designs/003`` §7）。
- 图片提取：``IMAGE_MIN_AREA`` / ``IMAGE_FORMAT``
- 页码检测：``PAGE_NUMBER_PATTERNS`` / ``PAGE_NUMBER_THRESHOLD_RATIO``
- OCR 栅格化：``OCR_DPI``（与 ``layout_filter`` 的版面坐标 DPI 是两套量纲，不可合并）

丢弃：旧 ``config.py`` 的 9 个 PaddleOCR 常量 + ``get_layout_kwargs``/``get_ocr_kwargs``
（OCR 模型参数属 ocr-extractor 内部，不放共享 config）。
"""

from __future__ import annotations

# ==================== 图片提取参数 ====================

# 图片最小面积阈值（PDF 坐标单位平方）。低于此面积视为装饰元素，不提取。
IMAGE_MIN_AREA: float = 1000.0

# 图片输出格式：png（无损）或 jpg
IMAGE_FORMAT: str = "png"

# ==================== 页码检测参数 ====================

# 页码正则列表（按优先级）。当多数页面（>=阈值比例）的底部元素匹配某正则时认为有效。
PAGE_NUMBER_PATTERNS: list[str] = [
    r"^\d+$",  # 纯数字：1, 2, 3...
    r"^第\s*\d+\s*页$",  # 中文格式：第 1 页
    r"^\d+\s*/\s*\d+$",  # 分数格式：1/10, 13 / 14
    r"^Page\s+\d+",  # 英文格式：Page 1
    r"^P\.?\s*\d+",  # 简写格式：P.1, P1
]

# 页码判定阈值：多少比例的页面匹配同一正则才认为是页码
PAGE_NUMBER_THRESHOLD_RATIO: float = 0.7

# ==================== OCR 栅格化参数 ====================

# 扫描件 PDF 转图像的 DPI（每英寸点数）。范围建议 150-300，越高越清晰但越慢。
# 注意：这与 layout_filter 内的 LAYOUT_DPI=136 / PDF_DPI=72（版面坐标换算）是两套量纲。
OCR_DPI: int = 200
