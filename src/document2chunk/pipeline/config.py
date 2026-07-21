"""span 管线共享常量。

仅保留提取前端在用的常量（designs/009）。
- 图片提取：``IMAGE_MIN_AREA`` / ``IMAGE_FORMAT``
- OCR 栅格化：``OCR_DPI``（与 ``layout_filter`` 的版面坐标 DPI 是两套量纲，不可合并）

页码检测常量已随 PageNumberDetectionStage 移除（页码改由
:func:`document2chunk.postprocess.filter_noise` 的序列检测处理）。
"""

from __future__ import annotations

# ==================== 图片提取参数 ====================

# 图片最小面积阈值（PDF 坐标单位平方）。低于此面积视为装饰元素，不提取。
IMAGE_MIN_AREA: float = 1000.0

# 图片输出格式：png（无损）或 jpg
IMAGE_FORMAT: str = "png"

# ==================== OCR 栅格化参数 ====================

# 扫描件 PDF 转图像的 DPI（每英寸点数）。范围建议 150-300，越高越清晰但越慢。
# 注意：这与 layout_filter 内的 LAYOUT_DPI=136 / PDF_DPI=72（版面坐标换算）是两套量纲。
OCR_DPI: int = 200

