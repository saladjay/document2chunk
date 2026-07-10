"""span 管线 9 个 Stage 实现（迁移自 doc-paddle-ocr，依据 designs/003 §4）。

Stage 顺序耦合（不可随意调换）：auto_level 的「≥0.50 跳过」依赖 classification
与 toc_analysis 已先行赋分；toc 页必须跳过 layout_filter 的启发式页眉页脚。
"""

from document2chunk.pipeline.stages.auto_level import AutoLevelStage
from document2chunk.pipeline.stages.body_analysis import BodyAnalysisStage
from document2chunk.pipeline.stages.classification import ClassificationStage
from document2chunk.pipeline.stages.image_detection import ImageDetectionStage
from document2chunk.pipeline.stages.layout_filter import (
    LayoutFilterStage,
    load_layout_data,
)
from document2chunk.pipeline.stages.merge import MergeStage
from document2chunk.pipeline.stages.page_number_detection import (
    PageNumberDetectionStage,
)
from document2chunk.pipeline.stages.toc_analysis import TOCAnalysisStage
from document2chunk.pipeline.stages.toc_detection import TOCDetectionStage

__all__ = [
    "BodyAnalysisStage",
    "ClassificationStage",
    "ImageDetectionStage",
    "LayoutFilterStage",
    "MergeStage",
    "AutoLevelStage",
    "TOCDetectionStage",
    "TOCAnalysisStage",
    "PageNumberDetectionStage",
    "load_layout_data",
]
