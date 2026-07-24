"""PDF 前端 Stage 实现（designs/009 全文档架构后仅保留 5 个 stage）。

文档级决策（跨页合并/噪声过滤/标题定级/附件拆分）已上移到
:mod:`document2chunk.postprocess`；版面几何工具函数仍在 layout_filter。
"""

from document2chunk.pipeline.stages.body_analysis import BodyAnalysisStage
from document2chunk.pipeline.stages.classification import ClassificationStage
from document2chunk.pipeline.stages.image_detection import ImageDetectionStage
from document2chunk.pipeline.stages.layout_filter import (
    load_layout_data,
    layout_boxes_for_page,
    layout_to_pdf_coords,
)
from document2chunk.pipeline.stages.merge import MergeStage
from document2chunk.pipeline.stages.toc_detection import TOCDetectionStage

__all__ = [
    "BodyAnalysisStage",
    "ClassificationStage",
    "ImageDetectionStage",
    "MergeStage",
    "TOCDetectionStage",
    "load_layout_data",
    "layout_boxes_for_page",
    "layout_to_pdf_coords",
]
