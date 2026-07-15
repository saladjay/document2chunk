"""pipeline 引擎测试（聚焦 designs/003 §9 的四个修复）。

运行：
    PYTHONPATH=src python tests/test_pipeline.py
"""

from __future__ import annotations

import os
import tempfile

from document2chunk.pipeline import (
    Pipeline,
    PipelineContext,
    SplitPipeline,
    SplitStages,
    default_split_stages,
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
)


# ---------- 辅助：记录型 Stage ----------


class _RecordStage:
    """记录 process 调用的最小 Stage（用于分组/注入测试）。"""

    def __init__(self, name, is_global=False):
        self._name = name
        self._is_global = is_global
        self.calls = 0

    @property
    def name(self):
        return self._name

    @property
    def is_global(self):
        return self._is_global

    def process(self, elements, ctx):
        self.calls += 1
        return elements


def _ctx(page_index=0, **kw):
    return PipelineContext(page_index=page_index, **kw)


# ---------- 1. _group_stages ----------


def test_group_stages():
    """连续相同 is_global 合段。"""
    p = Pipeline(
        [
            _RecordStage("g1", True),
            _RecordStage("g2", True),
            _RecordStage("l1", False),
            _RecordStage("l2", False),
            _RecordStage("g3", True),
        ]
    )
    groups = p._group_stages()
    assert groups == [
        (True, [p._stages[0], p._stages[1]]),
        (False, [p._stages[2], p._stages[3]]),
        (True, [p._stages[4]]),
    ], groups
    print("OK test_group_stages")


# ---------- 2. _redistribute 修复（page_index 键）----------


def test_redistribute_uses_page_index():
    """修复：兜底按 page_index（无下划线）分配；旧实现读 _page_index 几乎全归 page 0。"""
    elems = [
        {"text": "a", "page_index": 0},
        {"text": "b", "page_index": 2},
        {"text": "c", "page_index": 2},
        {"text": "d", "page_index": 1},
    ]
    result = Pipeline._redistribute(elems, 3)
    assert [e["text"] for e in result[0]] == ["a"], result[0]
    assert [e["text"] for e in result[1]] == ["d"], result[1]
    assert [e["text"] for e in result[2]] == ["b", "c"], result[2]
    # 旧实现读 _page_index（不存在）→ 全部归 page 0；这里验证不会如此
    assert result[0] != elems, "regression: all fell to page 0"
    print("OK test_redistribute_uses_page_index")


# ---------- 3. _DebugTracer 共享计数器 ----------


def test_split_pipeline_shared_debug_counter():
    """SplitPipeline 多个子管线共享一个 tracer → {NN}_{name}.json 连续编号。"""
    from document2chunk.extractors._mapping import _IdGen  # noqa: F401  (确保 mapping 可 import)

    pages = _two_simple_pages()
    dbg = tempfile.mkdtemp()
    sp = SplitPipeline(stages=default_split_stages(), debug_dir=dbg)
    sp.run(pages)

    files = sorted(os.listdir(dbg))
    # 至少有 body_analysis / classification / merge / auto_level 等
    names = [f[3:].removesuffix(".json") for f in files]  # 去 "NN_" 前缀
    assert "body_analysis" in names, names
    assert "classification" in names, names
    # 编号连续 01..N
    nums = [int(f[:2]) for f in files]
    assert nums == list(range(1, len(files) + 1)), nums
    print(f"OK test_split_pipeline_shared_debug_counter ({len(files)} files: {names})")


# ---------- 4. SplitStages 构造注入（DIP）----------


def test_split_stages_injection():
    """SplitPipeline 通过构造注入 SplitStages，不再延迟 import 具体 Stage。"""
    stages = SplitStages(
        body_analysis=BodyAnalysisStage(),
        image_detection=ImageDetectionStage(),
        classification=ClassificationStage(),
        toc_detection=TOCDetectionStage(),
        layout_filter=LayoutFilterStage(enable_heuristic_header_footer=False),
        toc_analysis=TOCAnalysisStage(),
        merge=MergeStage(),
        auto_level=AutoLevelStage(),
        page_number_detection=PageNumberDetectionStage(),
    )
    sp = SplitPipeline(stages=stages)
    # stage_names 不依赖延迟 import
    assert "body_analysis" in sp.stage_names
    pages = _two_simple_pages()
    out = sp.run(pages)
    assert len(out) == 2
    # 注入的 LayoutFilter 关闭了启发式 → 顶部标题不被误删
    flat = [e for page in out for e in page]
    assert any(e.get("type") in ("title", "heading") for e in flat), flat
    print("OK test_split_stages_injection")


# ---------- 5. saved_body 删除后 body 基准仍正确 ----------


def test_body_baseline_preserved_without_saved_body():
    """删 saved_body 后：SplitPipeline 全程 body_font/size 与单跑 BodyAnalysis 一致。"""
    pages = _two_simple_pages()
    sp = SplitPipeline(stages=default_split_stages())
    out = sp.run(pages)
    assert len(out) == 2
    for elems, ctx in pages:
        assert ctx.body_font == "Helvetica", ctx.body_font
        assert ctx.body_font_size == 12.0, ctx.body_font_size
    # 处理后仍含正文
    flat = [e for page in out for e in page]
    assert any(e.get("type") == "paragraph" for e in flat)
    print("OK test_body_baseline_preserved_without_saved_body")


# ---------- 夹具 ----------


def _two_simple_pages():
    """两页简单元素（标题 + 正文），供引擎测试。"""
    title0 = _elem("Chapter Title", 22, [72, 56, 260, 86], bold_flags=True)
    body0a = _elem("Body one.", 12, [72, 140, 160, 156])
    body0b = _elem("Body two.", 12, [72, 160, 160, 176])
    head1 = _elem("1.1 Next", 16, [72, 62, 150, 84])
    body1 = _elem("Page two body.", 12, [72, 120, 200, 136])
    return [
        ([title0, body0a, body0b], _ctx(0, page_width=595, page_height=842)),
        ([head1, body1], _ctx(1, page_width=595, page_height=842)),
    ]


def _elem(text, size, bbox, bold_flags=False):
    flags = 0x10 if bold_flags else 0
    return {
        "type": None,
        "label": "text_line",
        "level": None,
        "text": text,
        "markdown": text,
        "bbox": bbox,
        "order_index": 0,
        "page_index": 0,
        "style": {"font": "Helvetica", "size": float(size), "bold": bold_flags, "italic": False, "flags": flags},
        "spans": [
            {"text": text, "font": "Helvetica", "size": float(size), "bbox": bbox, "flags": flags}
        ],
    }


# ---------- runner ----------


def main():
    test_group_stages()
    test_redistribute_uses_page_index()
    test_split_pipeline_shared_debug_counter()
    test_split_stages_injection()
    test_body_baseline_preserved_without_saved_body()
    print("\nALL PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()
