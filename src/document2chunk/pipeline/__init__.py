"""span 处理管线引擎（PDF 结构重建前端，designs/009）。

为 pdf-extractor 共用的内部依赖。**不**依赖 ir-model（保持纯 span 引擎），
**不**依赖 PyMuPDF/PaddleOCR。文档级后处理见 :mod:`document2chunk.postprocess`。

核心组件：
- :class:`Pipeline`：线性编排引擎（按 is_global 自动分组）
- :class:`PipelineContext` / :class:`Stage`：上下文与接口
- 5 个 Stage（:mod:`document2chunk.pipeline.stages`）
- :func:`pdf_pipeline`：PDF 前端预定义组合
"""

from document2chunk.pipeline.base import (
    Pipeline,
    PipelineContext,
    Stage,
)
from document2chunk.pipeline.presets import pdf_pipeline
from document2chunk.pipeline.stages import (
    BodyAnalysisStage,
    ClassificationStage,
    ImageDetectionStage,
    MergeStage,
    TOCDetectionStage,
    load_layout_data,
)

__all__ = [
    # 引擎
    "Pipeline",
    "PipelineContext",
    "Stage",
    # Stage 实现
    "BodyAnalysisStage",
    "ClassificationStage",
    "ImageDetectionStage",
    "MergeStage",
    "TOCDetectionStage",
    "load_layout_data",
    # 预设
    "pdf_pipeline",
]
