"""span 管线编排引擎（designs/009 全文档架构）。

PDF 前端用线性 :class:`Pipeline`（5 stage），跨页/噪声/定级等文档级决策上移到
:mod:`document2chunk.postprocess`。引擎按 ``is_global`` 自动分组连续 stage 为
global/local 段交替执行。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

_logger = logging.getLogger("document2chunk.pipeline")


@dataclass
class PipelineContext:
    """Stage 间共享的上下文（可变状态容器）。

    每页一个独立实例；全局 stage 使用 ``Pipeline`` 内部的 ``shared_ctx``。
    字段契约见 ``designs/003`` §3（已删死字段 ``image_dir``/``pdf_stem``）。
    """

    # --- 页面级参数（每页独立） ---
    page_width: float = 0.0
    page_height: float = 0.0
    page_index: int = 0

    # --- 全局分析结果（由 BodyAnalysis 写入，所有页共享） ---
    body_font: str | None = None
    body_font_size: float | None = None

    # --- 版面分析数据（外部注入） ---
    layout_data: list[dict] | None = None

    # --- 图片提取数据（parser 上游注入，image_detection 防御读取） ---
    image_infos: list[dict] = field(default_factory=list)

    # --- 中间产物 ---
    style_char_counts: dict = field(default_factory=dict)
    max_heading_level: int = 0

    # --- 统计 ---
    stats: dict = field(default_factory=dict)

    # --- 源类型（供 source 感知 stage 分支；走 Provenance，不污染 ir-model） ---
    source_type: str = "pdf"


@runtime_checkable
class Stage(Protocol):
    """Stage 统一接口（Protocol，鸭子类型）。"""

    @property
    def name(self) -> str:
        """Stage 名称，用于日志和调试。"""
        ...

    @property
    def is_global(self) -> bool:
        """True = 跨页运行（收集所有页 elements 后执行一次）；False = 逐页运行。"""
        ...

    def process(
        self,
        elements: list[dict],
        ctx: PipelineContext,
    ) -> list[dict]:
        """处理 element 列表，返回新的 element 列表。"""
        ...


# ============================================================
# 调试追踪器（替代 _stage_counter 接力）
# ============================================================


class _DebugTracer:
    """共享的调试追踪器。

    持有 ``debug_dir`` 与一个单调递增的 stage 计数器，为每个 stage 写
    ``{NN}_{name}.json`` 中间结果。``debug_dir=None`` 时零开销。
    """

    def __init__(self, debug_dir: str | None = None):
        self.debug_dir = debug_dir
        self._counter = 0

    @property
    def enabled(self) -> bool:
        return self.debug_dir is not None

    def record(
        self,
        stage: Stage,
        page_elements: list[list[dict]],
        page_contexts: list[PipelineContext],
        page_offsets: list[int] | None,
    ) -> None:
        """保存某 stage 执行后的中间结果（schema 见 INTEGRATION §4）。"""
        if self.debug_dir is None:
            return

        self._counter += 1
        stage_name = stage.name
        filename = f"{self._counter:02d}_{stage_name}.json"
        filepath = Path(self.debug_dir) / filename

        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)

            if page_offsets is not None and len(page_offsets) > 1:
                # 全局 stage：page_elements 是合并后的扁平列表，按 offsets 切分
                all_elements = page_elements
                output_pages = []
                for i in range(len(page_contexts)):
                    start = page_offsets[i]
                    end = page_offsets[i + 1]
                    output_pages.append(
                        {
                            "page_index": page_contexts[i].page_index,
                            "elements": all_elements[start:end],
                        }
                    )
            else:
                # 局部 stage：page_elements 已按页分好
                output_pages = []
                for i, elems in enumerate(page_elements):
                    output_pages.append(
                        {
                            "page_index": page_contexts[i].page_index,
                            "elements": elems,
                        }
                    )

            record = {
                "stage_index": self._counter,
                "stage_name": stage_name,
                "stage_type": "global" if page_offsets is not None else "local",
                "pages": output_pages,
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)

        except Exception as e:
            # 调试保存失败不应中断主流程
            _logger.warning("保存中间结果失败 %s: %s", filepath, e)


# ============================================================
# Pipeline（线性编排）
# ============================================================


class Pipeline:
    """流水线编排引擎。

    按 stage 注册顺序执行，自动区分全局 / 局部 stage：
    - ``is_global=True``：收集所有页 elements 合并后执行一次；
    - ``is_global=False``：逐页执行。

    执行模型：将 stages 按 ``is_global`` 分组为连续段，遇到 global↔local 切换时
    先跑完当前组再跑下一组（:meth:`_group_stages`）。
    """

    def __init__(
        self,
        stages: list[Stage] | None = None,
        *,
        debug_dir: str | None = None,
        tracer: _DebugTracer | None = None,
    ):
        self._stages: list[Stage] = list(stages or [])
        # 共享 tracer：外部传入或按 debug_dir 新建
        self._tracer = tracer if tracer is not None else _DebugTracer(debug_dir)

    def add(self, stage: Stage) -> "Pipeline":
        """链式添加 stage。"""
        self._stages.append(stage)
        return self

    @property
    def stage_names(self) -> list[str]:
        return [s.name for s in self._stages]

    def run(
        self,
        pages: list[tuple[list[dict], PipelineContext]],
    ) -> list[list[dict]]:
        """执行流水线。

        Args:
            pages: ``[(page_elements, page_context), ...]`` 每页的原始元素和上下文。

        Returns:
            处理后的 ``[page_elements, ...]``，顺序与输入一致。
        """
        if not self._stages:
            return [elems for elems, _ in pages]

        groups = self._group_stages()

        page_elements = [elems for elems, _ in pages]
        page_contexts = [ctx for _, ctx in pages]

        # 全局共享 context（用于 global stage）
        shared_ctx = PipelineContext()
        if page_contexts:
            # layout_data 各页共享同一份；继承已算出的正文基准/层级（防子管线 shared_ctx 为空）
            shared_ctx.layout_data = page_contexts[0].layout_data
            shared_ctx.body_font = page_contexts[0].body_font
            shared_ctx.body_font_size = page_contexts[0].body_font_size
            shared_ctx.max_heading_level = page_contexts[0].max_heading_level
            shared_ctx.source_type = page_contexts[0].source_type

        for is_global_group, stages_in_group in groups:
            if is_global_group:
                page_elements = self._run_global_group(
                    stages_in_group, page_elements, page_contexts, shared_ctx
                )
            else:
                page_elements = self._run_local_group(
                    stages_in_group, page_elements, page_contexts
                )

        return page_elements

    def _run_global_group(
        self,
        stages: list[Stage],
        page_elements: list[list[dict]],
        page_contexts: list[PipelineContext],
        shared_ctx: PipelineContext,
    ) -> list[list[dict]]:
        # 注入 page_index 到每个元素，供全局 stage 按页分组
        for page_idx_local, elems in enumerate(page_elements):
            ctx_local = page_contexts[page_idx_local]
            for elem in elems:
                if "page_index" not in elem or elem["page_index"] is None:
                    elem["page_index"] = ctx_local.page_index

        all_elements: list[dict] = []
        page_offsets: list[int] = []  # 每页 elements 在合并列表中的起始位置
        offset = 0
        for elems in page_elements:
            page_offsets.append(offset)
            all_elements.extend(elems)
            offset += len(elems)
        page_offsets.append(offset)  # 末尾哨兵

        for stage in stages:
            all_elements = stage.process(all_elements, shared_ctx)
            self._tracer.record(stage, all_elements, page_contexts, page_offsets)

        # 全局 stage 透传不改元素数量（BodyAnalysis/AutoLevel/TOCAnalysis 满足），
        # 可安全按原 offset 切分
        new_page_elements = []
        for i in range(len(page_elements)):
            start = page_offsets[i]
            end = page_offsets[i + 1]
            new_page_elements.append(all_elements[start:end])

        # 兜底：若元素数量变了（理论上不应发生），按 page_index 重分
        if sum(len(e) for e in new_page_elements) != len(all_elements):
            new_page_elements = self._redistribute(
                all_elements, len(page_elements)
            )

        # 全局结果同步到每页 context
        for ctx in page_contexts:
            ctx.body_font = shared_ctx.body_font
            ctx.body_font_size = shared_ctx.body_font_size
            ctx.max_heading_level = shared_ctx.max_heading_level

        return new_page_elements

    def _run_local_group(
        self,
        stages: list[Stage],
        page_elements: list[list[dict]],
        page_contexts: list[PipelineContext],
    ) -> list[list[dict]]:
        for stage in stages:
            for page_idx in range(len(page_elements)):
                ctx = page_contexts[page_idx]
                page_elements[page_idx] = stage.process(
                    page_elements[page_idx], ctx
                )
            self._tracer.record(stage, page_elements, page_contexts, None)
        return page_elements

    def _group_stages(self) -> list[tuple[bool, list[Stage]]]:
        """将 stages 按 is_global 分组为连续段。"""
        if not self._stages:
            return []

        groups: list[tuple[bool, list[Stage]]] = []
        current_is_global = self._stages[0].is_global
        current_group: list[Stage] = []

        for stage in self._stages:
            if stage.is_global == current_is_global:
                current_group.append(stage)
            else:
                groups.append((current_is_global, current_group))
                current_is_global = stage.is_global
                current_group = [stage]

        groups.append((current_is_global, current_group))
        return groups

    @staticmethod
    def _redistribute(
        elements: list[dict], page_count: int
    ) -> list[list[dict]]:
        """按 page_index 将 elements 重新分配到各页（全局 stage 改变元素数量的兜底）。

        修复（designs/003 §2.3）：读 ``page_index``（无下划线）—— ``run`` 注入的
        正是这个键；旧实现读 ``_page_index`` 导致几乎全归 page 0。
        """
        result: list[list[dict]] = [[] for _ in range(page_count)]
        for elem in elements:
            page_idx = elem.get("page_index", 0)
            if 0 <= page_idx < page_count:
                result[page_idx].append(elem)
            else:
                result[0].append(elem)
        return result

