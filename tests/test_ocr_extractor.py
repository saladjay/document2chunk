"""ocr-extractor 测试（合成 fixture 匹配实测 schema；client 用 mock）。"""

from __future__ import annotations

import copy
import os
import tempfile

from document2chunk.extractors.ocr import OcrExtractor
from document2chunk.extractors.ocr._mapping import _Idc, build_page_blocks
from document2chunk.extractors.ocr._markdown import parse_markdown
from document2chunk.ir import (
    HeadingNode,
    ImageNode,
    ParagraphNode,
    SourceType,
    TableNode,
)

TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

MD = (
    "# 标题一\n\n"
    "这是正文。\n\n"
    "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>\n\n"
    "![图](ocr_images/img1.png)"
)

PRL = [
    {"block_label": "title", "block_order": 0, "block_content": "标题一", "block_bbox": [10, 10, 100, 30]},
    {"block_label": "text", "block_order": 1, "block_content": "这是正文。", "block_bbox": [10, 40, 100, 60]},
    {"block_label": "table", "block_order": 2, "block_content": "<table>", "block_bbox": [10, 70, 200, 120]},
    {"block_label": "image", "block_order": 3, "block_content": "", "block_bbox": [10, 130, 150, 180]},
]
IMAGES = {"ocr_images/img1.png": TINY_PNG_B64}

RESP = {
    "markdown": MD,
    "images": IMAGES,
    "layoutParsingResults": [
        {
            "page_index": 1,
            "page_count": 1,
            "markdown": {"text": MD, "images": IMAGES},
            "parsing_res_list": PRL,
        }
    ],
}


class FakeClient:
    def active_model(self):
        return "unlimited"

    def parse(self, media, filename, *, model):
        return copy.deepcopy(RESP)


def test_parse_markdown_elements():
    els = parse_markdown(MD)
    assert [e["kind"] for e in els] == ["heading", "paragraph", "table", "image"]
    assert els[0]["level"] == 1 and els[0]["text"] == "标题一"
    assert els[3]["ref"] == "ocr_images/img1.png"


def test_build_page_blocks_types_and_provenance():
    blocks = build_page_blocks(MD, PRL, IMAGES, 0, _Idc(), None, False, [0])
    assert len(blocks) == 4
    assert isinstance(blocks[0], HeadingNode) and blocks[0].level == 1
    assert isinstance(blocks[1], ParagraphNode)
    assert isinstance(blocks[2], TableNode)
    assert isinstance(blocks[3], ImageNode)
    # bbox 来自 parsing_res_list，page_index=0
    assert blocks[0].provenance.page_index == 0
    assert blocks[0].provenance.bbox == [10, 10, 100, 30]
    assert blocks[2].provenance.bbox == [10, 70, 200, 120]


def test_table_html_to_node():
    blocks = build_page_blocks(MD, PRL, IMAGES, 0, _Idc(), None, False, [0])
    t = blocks[2]
    assert len(t.rows) == 2
    assert t.rows[0].is_header is True
    assert t.rows[0].cells[0].blocks[0].text == "A"
    assert t.rows[1].cells[1].blocks[0].text == "2"


def test_image_saved_to_dir():
    with tempfile.TemporaryDirectory() as d:
        blocks = build_page_blocks(MD, PRL, IMAGES, 0, _Idc(), d, True, [0])
        assert blocks[3].image_id == "p0_1.png"
        assert os.path.exists(os.path.join(d, "p0_1.png"))


def test_drop_page_number_label():
    prl = PRL + [{"block_label": "page_number", "block_order": 4, "block_content": "1", "block_bbox": [0, 0, 10, 10]}]
    blocks = build_page_blocks(MD, prl, IMAGES, 0, _Idc(), None, False, [0])
    assert len(blocks) == 4  # page_number 不产块，且不破坏 1:1 关联
    assert blocks[0].provenance.bbox == [10, 10, 100, 30]


def test_extractor_with_mock():
    ext = OcrExtractor(client=FakeClient())
    result = ext.extract(b"FAKEIMAGEBYTES_NOT_PDF")
    assert result.metadata.source_type == SourceType.OCR
    assert result.metadata.page_count == 1
    assert len(result.content) == 4
    assert isinstance(result.content[0], HeadingNode)
    assert result.content[0].text == "标题一"


def test_multipage_pdf_chunking():
    """2 页 PDF → 按页切分，两页都处理（回归 PDF 魔数判断 bug）。"""
    import io

    import fitz

    d = fitz.open()
    for _ in range(2):
        p = d.new_page()
        p.insert_text((50, 72), "x")
    buf = io.BytesIO()
    d.save(buf)
    d.close()
    pdf2 = buf.getvalue()

    ext = OcrExtractor(client=FakeClient())
    result = ext.extract(pdf2)
    assert result.metadata.page_count == 2
    pages = {b.provenance.page_index for b in result.content if b.provenance}
    assert pages == {0, 1}, f"应处理两页，实际 page_index: {pages}"


if __name__ == "__main__":
    for fn in [
        test_parse_markdown_elements,
        test_build_page_blocks_types_and_provenance,
        test_table_html_to_node,
        test_image_saved_to_dir,
        test_drop_page_number_label,
        test_extractor_with_mock,
        test_multipage_pdf_chunking,
    ]:
        fn()
        print("ok:", fn.__name__)
    print("ALL OCR TESTS PASSED")
