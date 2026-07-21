"""pipeline 引擎测试（designs/009 全文档架构）。

覆盖线性 Pipeline 引擎（_group_stages / _redistribute / _DebugTracer）+
pdf_pipeline 5-stage 装配 + MergeStage 行间距防过度合并。

运行：
    PYTHONPATH=src python tests/test_pipeline.py
"""

from __future__ import annotations

import os
import tempfile

from document2chunk.pipeline import (
    Pipeline,
    PipelineContext,
    pdf_pipeline,
)
from document2chunk.pipeline.stages import (
    BodyAnalysisStage,
    ClassificationStage,
    ImageDetectionStage,
    MergeStage,
    TOCDetectionStage,
)


# ---------- 辅助：记录型 Stage ----------


class _RecordStage:
    """记录 process 调用的最小 Stage（用于分组测试）。"""

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
    assert result[0] != elems, "regression: all fell to page 0"
    print("OK test_redistribute_uses_page_index")


# ---------- 3. pdf_pipeline 线性装配 + 端到端跑通 ----------


def test_pdf_pipeline_linear():
    """pdf_pipeline 装配 5 个 stage 的线性 Pipeline；跑两页简单元素产出 heading + paragraph。"""
    pages = _two_simple_pages()
    dbg = tempfile.mkdtemp()
    pipe = pdf_pipeline(debug_dir=dbg)
    out = pipe.run(pages)
    assert len(out) == 2
    # 中间结果 {NN}_{name}.json 连续编号
    files = sorted(os.listdir(dbg))
    names = [f[3:].removesuffix(".json") for f in files]
    assert "body_analysis" in names, names
    assert "classification" in names, names
    assert "merge" in names, names
    nums = [int(f[:2]) for f in files]
    assert nums == list(range(1, len(files) + 1)), nums
    # 产出含 heading 与 paragraph
    flat = [e for page in out for e in page]
    assert any(e.get("type") in ("title", "heading") for e in flat), flat
    assert any(e.get("type") == "paragraph" for e in flat), flat
    # body 基准跨页一致
    for _elems, ctx in pages:
        assert ctx.body_font == "Helvetica", ctx.body_font
        assert ctx.body_font_size == 12.0, ctx.body_font_size
    print(f"OK test_pdf_pipeline_linear ({len(files)} stage dumps)")


# ---------- 4. SplitStages / saved_body 已随 SplitPipeline 移除（designs/009）----------


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


# ---------- 段落合并：行间距防过度合并 ----------


def test_merge_spacing_prevents_overmerge():
    """间距 > 标准行间距 × 阈值 → 分段；小间距 → 合并。"""
    def p(text, y0, y1):
        return {
            "type": "paragraph", "level": None, "text": text, "markdown": text,
            "bbox": [72, y0, 300, y1], "order_index": 0,
            "style": {"font": "SimSun", "size": 12.0, "flags": 0}, "spans": [],
        }

    ctx = PipelineContext(page_width=595, page_height=842)

    elems = [p("a", 0, 10), p("b", 12, 22), p("c", 24, 34),
             p("d", 44, 54), p("e", 56, 66), p("f", 68, 78)]
    assert MergeStage._compute_standard_spacing(elems) == 2.0
    out = MergeStage().process(elems, ctx)
    assert len(out) == 2, [o["text"] for o in out]
    assert out[0]["text"] == "abc" and out[1]["text"] == "def"

    assert len(MergeStage().process([p("a", 0, 10), p("b", 12, 22)], ctx)) == 1

    e = [p("a", 0, 10), {**p("b", 12, 22), "low_confidence": True}]
    assert MergeStage().process(e, ctx)[0].get("low_confidence") is True
    print("OK test_merge_spacing_prevents_overmerge")


# ---------- runner ----------


def main():
    test_group_stages()
    test_redistribute_uses_page_index()
    test_pdf_pipeline_linear()
    test_merge_spacing_prevents_overmerge()
    print("\nALL PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()
