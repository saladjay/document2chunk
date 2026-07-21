"""geo 重建测试（designs/008 §几何重建）。

覆盖：边聚类、幻影带修复、框→span、覆盖校验、行对齐、TableNode 构造、try_geo 回退、
extractor 集成（geo 优先 + html 回退 + mode），以及真机快照（skip-if-missing）。

运行：PYTHONPATH="src;tests" python tests/test_table_geo_reconstruct.py
"""

from __future__ import annotations

import os

from document2chunk.extractors.table._geo_reconstruct import (
    _decide_header,
    _default_tol,
    _parse_html_rows,
    _repair_phantom_bands,
    _snap_to_band,
    align_texts,
    boxes_to_grid,
    cluster_edges,
    geo_to_table_node,
    try_geo_to_table_node,
    validate_grid,
)
from document2chunk.extractors.table._html_parser import _Idc
from document2chunk.ir import SourceType, TableNode

# 真机数据（存在才跑快照）
_REAL = "D:/table/自然资规2019-1号/raw_service.json"


# ---------- 边聚类 ----------


def test_cluster_edges_basic():
    bands = cluster_edges([0, 1, 2, 10, 11, 12, 20], 3)
    assert len(bands) == 3
    assert abs(bands[0] - 1) < 0.6 and abs(bands[1] - 11) < 0.6 and abs(bands[2] - 20) < 0.6
    print("OK test_cluster_edges_basic")


def test_cluster_edges_empty():
    assert cluster_edges([], 5) == []
    assert cluster_edges([5], 5) == [5]
    print("OK test_cluster_edges_empty")


def test_default_tol_scales_with_dim():
    assert _default_tol([100, 100, 100]) == 18.0          # 0.18 × 100
    assert _default_tol([40, 40]) == 8.0                   # 0.18×40=7.2 → floor 8
    assert _default_tol([]) == 8.0
    print("OK test_default_tol_scales_with_dim")


# ---------- 幻影带修复 ----------


def test_repair_phantom_bands_merges_jitter():
    # 100 与 105 是同一线的抖动（间距 5 << median 95）→ 合并
    out = _repair_phantom_bands([0, 100, 105, 200])
    assert len(out) == 3                                  # 105 并入 100
    assert abs(out[0] - 0) < 1 and abs(out[2] - 200) < 1
    print("OK test_repair_phantom_bands_merges_jitter")


def test_repair_phantom_bands_keeps_uniform():
    # 等距真带（间距 100 ≈ median）→ 不合并
    out = _repair_phantom_bands([0, 100, 200, 300])
    assert len(out) == 4
    print("OK test_repair_phantom_bands_keeps_uniform")


# ---------- snap ----------


def test_snap_to_band():
    bands = [0, 5, 10]
    assert _snap_to_band(5.5, bands) == 1                 # 近 5
    assert _snap_to_band(15, bands) == 2                  # 越界 → 末
    assert _snap_to_band(0, bands) == 0
    assert _snap_to_band(3, []) == 0                      # 空 → 0
    print("OK test_snap_to_band")


# ---------- boxes_to_grid ----------


def test_boxes_to_grid_clean_2x2():
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    g = boxes_to_grid(boxes)
    assert (g["rows"], g["cols"]) == (2, 2)
    assert all(c["rowspan"] == 1 and c["colspan"] == 1 for c in g["cells"])
    ok, _ = validate_grid(g)
    assert ok
    print("OK test_boxes_to_grid_clean_2x2")


def test_boxes_to_grid_rowspan3():
    # 左列通栏 3 行 + 右列 3 个 1×1
    boxes = [[0, 0, 10, 30], [10, 0, 20, 10], [10, 10, 20, 20], [10, 20, 20, 30]]
    g = boxes_to_grid(boxes)
    assert (g["rows"], g["cols"]) == (3, 2)
    left = next(c for c in g["cells"] if c["c0"] == 0)
    assert left["rowspan"] == 3 and left["colspan"] == 1
    ok, _ = validate_grid(g)
    assert ok
    print("OK test_boxes_to_grid_rowspan3")


def test_boxes_to_grid_degenerate_single():
    g = boxes_to_grid([[0, 0, 10, 10]])
    ok, reason = validate_grid(g)
    assert not ok and reason == "too_few_cells"
    print("OK test_boxes_to_grid_degenerate_single")


def test_validate_grid_overlaps_fail():
    # 3×3 全格重复一遍 → 每格被覆盖两次，overlaps=9 远超 slack
    cells = [{"r0": r, "c0": c, "rowspan": 1, "colspan": 1} for r in range(3) for c in range(3)] * 2
    ok, reason = validate_grid({"rows": 3, "cols": 3, "cells": cells})
    assert not ok and reason.startswith("overlaps")
    print("OK test_validate_grid_overlaps_fail")


# ---------- 文字行对齐 ----------


def test_parse_html_rows_filters_empty_keeps_order():
    html = "<table><tr><td></td><td>A</td></tr><tr><td>B</td><td></td></tr></table>"
    assert _parse_html_rows(html) == [["A"], ["B"]]
    print("OK test_parse_html_rows_filters_empty_keeps_order")


def test_align_texts_rowwise():
    cells = [
        {"r0": 0, "c0": 0}, {"r0": 0, "c0": 1},
        {"r0": 1, "c0": 0}, {"r0": 1, "c0": 1},
    ]
    al = align_texts(cells, [["A", "B"], ["C", "D"]])
    assert al["pairs"] == {0: "A", 1: "B", 2: "C", 3: "D"}
    print("OK test_align_texts_rowwise")


def test_align_texts_more_texts_than_cells_returns_none():
    cells = [{"r0": 0, "c0": 0}]
    assert align_texts(cells, [["A", "B", "C"]]) is None
    print("OK test_align_texts_more_texts_than_cells_returns_none")


# ---------- 表头判定 ----------


def test_decide_header():
    merged = [{"rowspan": 2, "colspan": 1}, {"rowspan": 1, "colspan": 2}]
    assert _decide_header(0, [], header_first_row=True, header_rows=None) is True
    assert _decide_header(2, [], header_first_row=True, header_rows=3) is True
    assert _decide_header(3, [], header_first_row=True, header_rows=3) is False
    # 全合并行（无 header_rows、非首行）→ 表头
    assert _decide_header(1, merged, header_first_row=False, header_rows=None) is True
    print("OK test_decide_header")


# ---------- geo_to_table_node ----------


def test_geo_to_table_node_basic():
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    t = geo_to_table_node(boxes, html=html, page_index=2)
    assert isinstance(t, TableNode) and len(t.rows) == 2
    # 每格 bbox == 输入框；RunNode.text == ParagraphNode.text
    c00 = t.rows[0].cells[0]
    assert c00.blocks[0].provenance.bbox == [0, 0, 10, 10]
    assert c00.blocks[0].text == "A" and c00.blocks[0].runs[0].text == "A"
    assert t.provenance.page_index == 2
    print("OK test_geo_to_table_node_basic")


def test_geo_to_table_node_spans_and_bbox_union():
    boxes = [[0, 0, 10, 30], [10, 0, 20, 10], [10, 10, 20, 20], [10, 20, 20, 30]]
    html = "<table><tr><td>L</td><td>a</td></tr><tr><td>b</td></tr><tr><td>c</td></tr></table>"
    t = geo_to_table_node(boxes, html=html)
    left = t.rows[0].cells[0]
    assert left.rowspan == 3 and left.colspan == 1
    # table bbox 缺省取并集
    assert t.provenance.bbox == [0, 0, 20, 30]
    print("OK test_geo_to_table_node_spans_and_bbox_union")


def test_geo_to_table_node_header_rows():
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    t = geo_to_table_node(boxes, html=html, header_first_row=False, header_rows=1)
    assert t.rows[0].is_header is True and t.rows[1].is_header is False
    print("OK test_geo_to_table_node_header_rows")


def test_geo_to_table_node_idc_unique_across_calls():
    idc = _Idc()
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    n1 = geo_to_table_node(boxes, html=html, idc=idc)
    n2 = geo_to_table_node(boxes, html=html, idc=idc)

    def _ids(node):
        out = [node.id]
        for r in node.rows:
            out.append(r.id)
            for c in r.cells:
                out.append(c.id)
                out.append(c.blocks[0].id)
                out.append(c.blocks[0].runs[0].id)
        return out

    all_ids = _ids(n1) + _ids(n2)
    assert len(all_ids) == len(set(all_ids))             # 共享 idc → 跨调用全唯一
    print("OK test_geo_to_table_node_idc_unique_across_calls")


# ---------- try_geo 回退 ----------


def test_try_geo_none_on_degenerate():
    assert try_geo_to_table_node([[0, 0, 10, 10]]) is None
    assert try_geo_to_table_node(None) is None
    print("OK test_try_geo_none_on_degenerate")


def test_try_geo_none_on_text_mismatch():
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    html = "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr></table>"
    assert try_geo_to_table_node(boxes, html=html) is None
    print("OK test_try_geo_none_on_text_mismatch")


def test_try_geo_none_on_overlapping_boxes():
    # 干净 3×3 + 复制对角 3 格 → 3 处重叠 > slack(max(2, 5%×9)=2) → 覆盖校验失败 → 回退
    base = [[x * 10, y * 10, x * 10 + 10, y * 10 + 10] for y in range(3) for x in range(3)]
    boxes = base + [base[0], base[4], base[8]]
    assert try_geo_to_table_node(boxes, html="<table><tr><td>A</td></tr></table>") is None
    print("OK test_try_geo_none_on_overlapping_boxes")


def test_try_geo_swallows_malformed():
    # 畸形 box（长度不足）→ 内部异常被吞，返回 None
    assert try_geo_to_table_node([[1, 2, 3]], html="") is None
    print("OK test_try_geo_swallows_malformed")


# ---------- extractor 集成 ----------


class _Stub:
    """复用 test_table_extractor 的 stub 范式。"""

    def __init__(self, tables):
        self._t = tables

    def recognize(self, data, filename, *, fmt=None, page_range="all"):
        return {"tables": self._t, "count": len(self._t), "formats": ["html", "json"]}


def _pdf() -> bytes:
    return b"%PDF-1.5 dummy"


def test_extractor_geo_path_used():
    from document2chunk.extractors.table.extractor import TableExtractor

    # 干净 2×2 cell_box_list → geo 产物（行/列来自几何）
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"
    tables = [{"page": 0, "html": html, "json": {"cell_box_list": boxes}}]
    r = TableExtractor(client=_Stub(tables)).extract(_pdf())
    t = r.content[0]
    assert len(t.rows) == 2 and len(t.rows[0].cells) == 2
    assert t.rows[0].cells[0].blocks[0].provenance.bbox == [0, 0, 10, 10]  # geo 产物带几何 bbox
    print("OK test_extractor_geo_path_used")


def test_extractor_fallback_to_html_on_overlapping():
    from document2chunk.extractors.table.extractor import TableExtractor

    # 3×3 + 复制对角 3 格 → 覆盖校验失败 → geo 回退 html（html 产物 cell 无几何 bbox）
    base = [[x * 10, y * 10, x * 10 + 10, y * 10 + 10] for y in range(3) for x in range(3)]
    boxes = base + [base[0], base[4], base[8]]
    html = '<table><tr><td colspan="2">H</td></tr><tr><td>a</td><td>b</td></tr></table>'
    tables = [{"page": 0, "html": html, "json": {"cell_box_list": boxes}}]
    r = TableExtractor(client=_Stub(tables)).extract(_pdf())
    t = r.content[0]
    # html 回退判据：3×3 几何上首格本应 colspan=1，但 html 的 colspan="2" 属性被保留 → 必是 html 路径
    assert t.rows[0].cells[0].colspan == 2
    print("OK test_extractor_fallback_to_html_on_overlapping")


def test_extractor_html_mode_skips_geo():
    from document2chunk.extractors.table.extractor import TableExtractor

    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    html = '<table><tr><td colspan="2">H</td></tr><tr><td>a</td><td>b</td></tr></table>'
    tables = [{"page": 0, "html": html, "json": {"cell_box_list": boxes}}]
    r = TableExtractor(client=_Stub(tables)).extract(_pdf(), options={"table_reconstruct": "html"})
    t = r.content[0]
    # mode=html：直走 html；2×2 几何首格本应 colspan=1，html 的 colspan="2" 被保留 → 证实跳过 geo
    assert t.rows[0].cells[0].colspan == 2
    print("OK test_extractor_html_mode_skips_geo")


# ---------- 真机快照（skip-if-missing）----------


def test_real_form_table_snapshot():
    if not os.path.exists(_REAL):
        print("SKIP test_real_form_table_snapshot（无真机数据 %s）" % _REAL)
        return
    import json

    raw = json.load(open(_REAL, encoding="utf-8"))
    t0 = raw["tables"][0]
    j = t0["json"]
    node = try_geo_to_table_node(j["cell_box_list"], html=t0["html"], page_index=19, header_rows=5)
    assert node is not None, "p19 应能 geo 重建"
    assert len(node.rows) == 8                                       # 8 行
    # 左列通栏（rowspan >= 3）
    left = node.rows[0].cells[0]
    assert left.rowspan >= 3
    # 首格文字是表头分组
    assert "永久基本农田" in node.rows[0].cells[0].blocks[0].text
    print("OK test_real_form_table_snapshot (8 行, 左列 rowspan=%d)" % left.rowspan)


def main():
    test_cluster_edges_basic()
    test_cluster_edges_empty()
    test_default_tol_scales_with_dim()
    test_repair_phantom_bands_merges_jitter()
    test_repair_phantom_bands_keeps_uniform()
    test_snap_to_band()
    test_boxes_to_grid_clean_2x2()
    test_boxes_to_grid_rowspan3()
    test_boxes_to_grid_degenerate_single()
    test_validate_grid_overlaps_fail()
    test_parse_html_rows_filters_empty_keeps_order()
    test_align_texts_rowwise()
    test_align_texts_more_texts_than_cells_returns_none()
    test_decide_header()
    test_geo_to_table_node_basic()
    test_geo_to_table_node_spans_and_bbox_union()
    test_geo_to_table_node_header_rows()
    test_geo_to_table_node_idc_unique_across_calls()
    test_try_geo_none_on_degenerate()
    test_try_geo_none_on_text_mismatch()
    test_try_geo_none_on_overlapping_boxes()
    test_try_geo_swallows_malformed()
    test_extractor_geo_path_used()
    test_extractor_fallback_to_html_on_overlapping()
    test_extractor_html_mode_skips_geo()
    test_real_form_table_snapshot()
    print("\nALL GEO RECONSTRUCT TESTS PASSED")


if __name__ == "__main__":
    main()
