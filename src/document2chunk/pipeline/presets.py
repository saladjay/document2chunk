"""预定义的 Pipeline 组合。

仅保留 ``default_pipeline``（线性）与 ``split_pipeline``（生产入口，目录页/正文页分流）。
丢弃旧 ``full_pipeline``/``simple_pipeline``（未用预设，designs/002 §3）。
"""

from __future__ import annotations

from document2chunk.pipeline.base import Pipeline, SplitPipeline, SplitStages
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
)


def default_pipeline(
    layout_jsonl: str | None = None,
    *,
    debug_dir: str | None = None,
) -> Pipeline:
    """标准线性流水线（版面过滤 + 目录识别/分析 + 自动层级 + 页码检测）。

    注意：生产入口是 :func:`split_pipeline`（目录页/正文页分流）；
    ``default_pipeline`` 与 ``SplitPipeline`` 的 LayoutFilter 位置、TOCAnalysis
    作用域都不同（designs/003 §2.5）。
    """
    return Pipeline(
        [
            BodyAnalysisStage(),
            ImageDetectionStage(),
            ClassificationStage(),
            LayoutFilterStage(
                layout_jsonl=layout_jsonl,
                enable_heuristic_header_footer=True,
            ),
            TOCDetectionStage(),
            TOCAnalysisStage(),
            MergeStage(),
            AutoLevelStage(),
            PageNumberDetectionStage(),
        ],
        debug_dir=debug_dir,
    )


def default_split_stages(
    layout_jsonl: str | None = None,
) -> SplitStages:
    """装配默认的 9 个 Stage 实例（SplitPipeline 构造注入用）。"""
    return SplitStages(
        body_analysis=BodyAnalysisStage(),
        image_detection=ImageDetectionStage(),
        classification=ClassificationStage(),
        toc_detection=TOCDetectionStage(),
        layout_filter=LayoutFilterStage(
            layout_jsonl=layout_jsonl,
            enable_heuristic_header_footer=True,
        ),
        toc_analysis=TOCAnalysisStage(),
        merge=MergeStage(),
        auto_level=AutoLevelStage(),
        page_number_detection=PageNumberDetectionStage(),
    )


def split_pipeline(
    layout_jsonl: str | None = None,
    *,
    debug_dir: str | None = None,
) -> SplitPipeline:
    """分流流水线（生产入口）：目录页走轻量管线，正文页走完整管线。"""
    return SplitPipeline(
        stages=default_split_stages(layout_jsonl),
        debug_dir=debug_dir,
    )
