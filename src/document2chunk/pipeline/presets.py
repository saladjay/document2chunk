"""预定义 Pipeline 组合（designs/009 全文档架构）。

PDF 前端只做「提取 + 标题类型判定（font 信号）+ 页内合并」，5 个 stage 的线性 Pipeline：
``BodyAnalysis → ImageDetection → Classification → TOCDetection → MergeStage``。
跨页合并 / 噪声过滤 / 标题定级 / 附件拆分等文档级决策全部上移到
:mod:`document2chunk.postprocess`（两路共用，在 BlockNode 层执行）。
"""

from __future__ import annotations

from document2chunk.pipeline.base import Pipeline
from document2chunk.pipeline.stages import (
    BodyAnalysisStage,
    ClassificationStage,
    ImageDetectionStage,
    MergeStage,
    TOCDetectionStage,
)


def pdf_pipeline(
    layout_jsonl: str | None = None,
    *,
    debug_dir: str | None = None,
) -> Pipeline:
    """PDF 前端线性管线（5 stage）。

    Args:
        layout_jsonl: 版面分析 JSONL（image_detection 用于图分类）。
        debug_dir: 管线调试目录（每 stage 写 {NN}_{name}.json）。
    """
    return Pipeline(
        [
            BodyAnalysisStage(),
            ImageDetectionStage(),
            ClassificationStage(),
            TOCDetectionStage(),
            MergeStage(),
        ],
        debug_dir=debug_dir,
    )
