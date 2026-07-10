"""span 处理管线引擎（PDF/OCR 结构重建前端）。

迁移自 ``doc-paddle-ocr/pdf_parsers/pipeline``（依据 designs/002、designs/003），
为 pdf-extractor / ocr-extractor 共用的内部依赖。**不**依赖 ir-model（保持纯 span 引擎），
**不**依赖 PyMuPDF/PaddleOCR（格式提取前端在各自 extractor 内）。

核心组件：
- :class:`Pipeline` / :class:`SplitPipeline`：编排引擎
- :class:`PipelineContext` / :class:`Stage` / :class:`SplitStages`：上下文与接口
- 9 个 Stage（:mod:`document2chunk.pipeline.stages`）
- :mod:`document2chunk.pipeline.heading_scorer` / :mod:`document2chunk.pipeline.common`
- :func:`default_pipeline` / :func:`split_pipeline`：预定义组合
"""

from document2chunk.pipeline.base import (
    Pipeline,
    PipelineContext,
    SplitPipeline,
    SplitStages,
    Stage,
)
from document2chunk.pipeline.presets import (
    default_pipeline,
    default_split_stages,
    split_pipeline,
)
from document2chunk.pipeline.stages import (
    AutoLevelStage,
    BodyAnalysisStage,
    ClassificationStage,
    ImageDetectionStage,
    LayoutFilterStage,
    MergeStage,
    PageNumberDetectionStage,
    TOCAnalysisStage,
    TOCDetectionStage,
    load_layout_data,
)

__all__ = [
    # 引擎
    "Pipeline",
    "PipelineContext",
    "SplitPipeline",
    "SplitStages",
    "Stage",
    # Stage 实现
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
    # 预设
    "default_pipeline",
    "default_split_stages",
    "split_pipeline",
]
