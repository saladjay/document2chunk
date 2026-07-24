"""geo_ocr 测试（designs/008 §几何重建 → box-bearing OCR）。

覆盖：``_cell_ocr.ocr_cell_texts``（poly 中心落点匹配、多 poly 拼接、无命中）、
``geo_ocr_to_table_node`` / ``try_geo_ocr_to_table_node``、extractor ``geo_ocr`` 模式
（图片源端到端 + 回退），以及真机快照（p19 r0 宽表头，skip-if-missing）。

运行：PYTHONPATH="src;tests" python tests/test_table_geo_ocr.py
"""

from __future__ import annotations

import os
from io import BytesIO

from document2chunk.extractors.table._cell_ocr import ocr_cell_texts
from document2chunk.extractors.table._geo_reconstruct import (
    geo_ocr_to_table_node,
    try_geo_ocr_to_table_node,
)
from document2chunk.ir import TableNode

_REAL_IMG = "D:/table/自然资规2019-1号/_p19_rotA.png"
_REAL_CALL = "D:/table/自然资规2019-1号/_imagecall.json"


class _StubOCR:
    """stub paddleocr 引擎：predict 返回固定 polys/texts。"""

    def __init__(self, polys, texts):
        self.polys = polys
        self.texts = texts

    def predict(self, image):
        return [{"rec_polys": self.polys, "rec_texts": self.texts}]


class _BoomOCR:
    def predict(self, image):
        raise RuntimeError("ocr down")


# ---------- ocr_cell_texts ----------


def test_ocr_cell_texts_center_match():
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10]]
    polys = [[[2, 2], [5, 2], [5, 5], [2, 5]], [[12, 2], [15, 2], [15, 5], [12, 5]]]
    out = ocr_cell_texts(boxes, "img", ocr=_StubOCR(polys, ["A", "B"]))
    assert out == {0: "A", 1: "B"}
    print("OK test_ocr_cell_texts_center_match")


def test_ocr_cell_texts_multi_concat_reading_order():
    # 一格内两个 poly：左 (x0=2) + 右 (x0=12)，按 (y0,x0) 排序 → "AB"
    boxes = [[0, 0, 20, 10]]
    polys = [[[12, 2], [15, 2], [15, 5], [12, 5]], [[2, 2], [5, 2], [5, 5], [2, 5]]]
    out = ocr_cell_texts(boxes, "img", ocr=_StubOCR(polys, ["B", "A"]))
    assert out == {0: "AB"}
    print("OK test_ocr_cell_texts_multi_concat_reading_order")


def test_ocr_cell_texts_no_hit_omitted():
    boxes = [[0, 0, 10, 10]]
    polys = [[[50, 50], [55, 50], [55, 55], [50, 55]]]  # 中心在格外
    assert ocr_cell_texts(boxes, "img", ocr=_StubOCR(polys, ["X"])) == {}
    print("OK test_ocr_cell_texts_no_hit_omitted")


def test_ocr_cell_texts_vertical_stack_order():
    # 一格内上下两 poly：上 (y0=2) + 下 (y0=12)，按 y 排序 → "上在下前"
    boxes = [[0, 0, 10, 30]]
    polys = [[[2, 12], [5, 12], [5, 15], [2, 15]], [[2, 2], [5, 2], [5, 5], [2, 5]]]
    out = ocr_cell_texts(boxes, "img", ocr=_StubOCR(polys, ["下", "上"]))
    assert out == {0: "上下"}
    print("OK test_ocr_cell_texts_vertical_stack_order")


# ---------- geo_ocr_to_table_node ----------


_BOXES_2X2 = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]


def test_geo_ocr_to_table_node_basic():
    polys = [[[2, 2], [5, 2], [5, 5], [2, 5]]]  # 中心 (3.5,3.5) → cell0
    t = geo_ocr_to_table_node(_BOXES_2X2, "img", ocr=_StubOCR(polys, ["A"]), page_index=1)
    assert isinstance(t, TableNode)
    c00 = t.rows[0].cells[0]
    assert c00.blocks[0].text == "A"
    assert c00.blocks[0].provenance.bbox == [0, 0, 10, 10]  # box-bearing
    assert c00.blocks[0].runs[0].text == "A"
    assert t.rows[0].cells[1].blocks[0].text == ""  # 无 OCR 命中 → 空
    assert t.provenance.page_index == 1
    print("OK test_geo_ocr_to_table_node_basic")


def test_geo_ocr_to_table_node_wide_cell_gets_header_text():
    # row0 一个宽 colspan2 格 + row1 两个普通格；OCR poly 落在宽格内 → 宽格得表头文字（根治 r0）
    boxes = [[0, 0, 30, 10], [0, 10, 15, 20], [15, 10, 30, 20]]
    polys = [[[10, 3], [20, 3], [20, 7], [10, 7]]]  # 中心 (15,5) 落在宽格 [0,0,30,10]
    t = geo_ocr_to_table_node(boxes, "img", ocr=_StubOCR(polys, ["表头"]))
    wide = next(c for r in t.rows for c in r.cells if c.colspan >= 2)
    assert wide.blocks[0].text == "表头"
    print("OK test_geo_ocr_to_table_node_wide_cell_gets_header_text")


# ---------- try_geo_ocr 回退 ----------


def test_try_geo_ocr_none_on_degenerate():
    assert try_geo_ocr_to_table_node([[0, 0, 10, 10]], "img", ocr=_StubOCR([], [])) is None
    assert try_geo_ocr_to_table_node(None, "img", ocr=_StubOCR([], [])) is None
    print("OK test_try_geo_ocr_none_on_degenerate")


def test_try_geo_ocr_none_on_ocr_exception():
    # OCR 抛异常 → 吞掉返回 None（不阻断流水线）
    assert (
        try_geo_ocr_to_table_node(_BOXES_2X2, "img", ocr=_BoomOCR()) is None
    )
    print("OK test_try_geo_ocr_none_on_ocr_exception")


# ---------- extractor geo_ocr 模式（图片源，免 fitz）----------


def _png_bytes(w=30, h=20):
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_extractor_geo_ocr_image_source():
    from document2chunk.extractors.table.extractor import TableExtractor

    img_bytes = _png_bytes()
    polys = [[[2, 2], [5, 2], [5, 5], [2, 5]]]
    html = "<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>"

    class _Stub:
        def recognize(self, data, filename, *, fmt=None, page_range="all"):
            return {
                "tables": [{"page": 0, "html": html, "json": {"cell_box_list": _BOXES_2X2}}],
                "count": 1,
                "formats": ["html", "json"],
            }

    r = TableExtractor(client=_Stub()).extract(
        img_bytes, options={"table_reconstruct": "geo_ocr", "ocr": _StubOCR(polys, ["A"])}
    )
    t = r.content[0]
    # geo_ocr 产物：cell0 文字来自 OCR = "A"（非 html 的 "A"——这里巧合相同，
    # 用 bbox 区分：geo_ocr cell 带几何 box，html 产物带 cell_boxes 时也有；改用文字来源校验）
    assert t.rows[0].cells[0].blocks[0].text == "A"
    assert r.metadata.custom["table_reconstruct"] == "geo_ocr"
    print("OK test_extractor_geo_ocr_image_source")


def test_extractor_geo_ocr_fallback_when_ocr_boom():
    from document2chunk.extractors.table.extractor import TableExtractor

    img_bytes = _png_bytes()
    # 重叠 box（3×3 + 对角复制）→ geo 与 geo_ocr 都失败 → 回退 html
    base = [[x * 10, y * 10, x * 10 + 10, y * 10 + 10] for y in range(3) for x in range(3)]
    boxes = base + [base[0], base[4], base[8]]
    html = '<table><tr><td colspan="2">H</td></tr><tr><td>a</td><td>b</td></tr></table>'

    class _Stub:
        def recognize(self, data, filename, *, fmt=None, page_range="all"):
            return {
                "tables": [{"page": 0, "html": html, "json": {"cell_box_list": boxes}}],
                "count": 1,
                "formats": ["html", "json"],
            }

    # OCR 崩 + geo 覆盖失败 → html 回退；首格 colspan=2 来自 html
    r = TableExtractor(client=_Stub()).extract(
        img_bytes, options={"table_reconstruct": "geo_ocr", "ocr": _BoomOCR()}
    )
    t = r.content[0]
    assert t.rows[0].cells[0].colspan == 2  # html 回退
    print("OK test_extractor_geo_ocr_fallback_when_ocr_boom")


# ---------- 真机快照：p19 r0 宽表头（skip-if-missing）----------


def test_real_p19_geo_ocr_r0_header():
    if not (os.path.exists(_REAL_IMG) and os.path.exists(_REAL_CALL)):
        print("SKIP test_real_p19_geo_ocr_r0_header（无真机数据）")
        return
    try:
        from paddleocr import PaddleOCR  # noqa: F401
    except ImportError:
        print("SKIP test_real_p19_geo_ocr_r0_header（无 paddleocr）")
        return
    import json

    from paddleocr import PaddleOCR

    call = json.load(open(_REAL_CALL, encoding="utf-8"))
    boxes = call["tables"][0]["json"]["cell_box_list"]
    ocr = PaddleOCR(lang="ch", use_textline_orientation=True)
    node = try_geo_ocr_to_table_node(
        boxes, _REAL_IMG, ocr=ocr, page_index=19, header_rows=5
    )
    assert node is not None, "p19 应能 geo_ocr 重建"
    # r0 必有宽 colspan 格承载「永久基本农田中耕地情况」类表头文字
    r0_texts = [c.blocks[0].text for c in node.rows[0].cells]
    assert any("永久基本农田" in t and "耕地情况" in t for t in r0_texts), r0_texts
    # 名优特新/非可调整 等之前缺失的列应被 OCR 找回
    all_text = "".join(c.blocks[0].text for r in node.rows for c in r.cells)
    assert "名优特新" in all_text or "可调整" in all_text
    print("OK test_real_p19_geo_ocr_r0_header (r0 表头落位正确，缺失列已找回)")


def main():
    test_ocr_cell_texts_center_match()
    test_ocr_cell_texts_multi_concat_reading_order()
    test_ocr_cell_texts_no_hit_omitted()
    test_ocr_cell_texts_vertical_stack_order()
    test_geo_ocr_to_table_node_basic()
    test_geo_ocr_to_table_node_wide_cell_gets_header_text()
    test_try_geo_ocr_none_on_degenerate()
    test_try_geo_ocr_none_on_ocr_exception()
    test_extractor_geo_ocr_image_source()
    test_extractor_geo_ocr_fallback_when_ocr_boom()
    test_real_p19_geo_ocr_r0_header()
    print("\nALL GEO_OCR TESTS PASSED")


if __name__ == "__main__":
    main()
