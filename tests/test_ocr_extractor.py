"""ocr-extractor 测试（合成 fixture 匹配实测 schema；client 用 mock）。"""

from __future__ import annotations

import copy
import os
import tempfile

from document2chunk.extractors.ocr import OcrExtractor
from document2chunk.extractors.ocr._mapping import _Idc, build_page_blocks
from document2chunk.extractors.ocr._markdown import parse_markdown
from document2chunk.ir import (
    FormulaNode,
    HeadingNode,
    ImageNode,
    InlineFormulaNode,
    ListNode,
    ParagraphNode,
    RunNode,
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
    {"block_label": "doc_title", "block_order": 0, "block_content": "# 标题一", "block_bbox": [10, 10, 100, 30]},
    {"block_label": "text", "block_order": 1, "block_content": "这是正文。", "block_bbox": [10, 40, 100, 60]},
    {"block_label": "table", "block_order": 2, "block_content": "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>", "block_bbox": [10, 70, 200, 120]},
    {"block_label": "image", "block_order": 3, "block_content": '<div style="text-align: center;"><img src="ocr_images/img1.png" alt="图" /></div>', "block_bbox": [10, 130, 150, 180]},
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


def test_block_and_inline_formulas():
    """块 \\[ .. \\] → FormulaNode；行内 \\( .. \\) → ParagraphNode.runs 里的 InlineFormulaNode。"""
    md = "# 公式示例\n\n\\[\nE = mc^2\n\\]\n\n行内 \\(x = 1\\) 公式。\n"
    prl = [
        {"block_label": "title", "block_order": 0, "block_content": "公式示例", "block_bbox": [0, 0, 10, 10]},
        {"block_label": "equation", "block_order": 1, "block_content": "E = mc^2", "block_bbox": [0, 20, 10, 30]},
        {"block_label": "text", "block_order": 2, "block_content": "行内 \\(x = 1\\) 公式。", "block_bbox": [0, 40, 10, 50]},
    ]
    blocks = build_page_blocks(md, prl, {}, 0, _Idc(), None, False, [0])
    assert len(blocks) == 3
    # 块公式
    assert isinstance(blocks[1], FormulaNode)
    assert blocks[1].latex == "E = mc^2"
    assert blocks[1].provenance.bbox == [0, 20, 10, 30]  # equation 块 bbox
    # 行内公式：段落 runs = Run / InlineFormula / Run
    p = blocks[2]
    assert isinstance(p, ParagraphNode)
    kinds = [type(r).__name__ for r in p.runs]
    assert kinds == ["RunNode", "InlineFormulaNode", "RunNode"], kinds
    assert p.runs[1].latex == "x = 1"


def test_bbox_calibration_to_page_coords():
    """OCR 服务 1000 归一化 bbox → PDF 点空间（debug 可视化坐标校准）。"""
    md = "# T\n\np"
    prl = [
        {"block_label": "title", "block_order": 0, "block_content": "T", "block_bbox": [165, 234, 787, 296]},
        {"block_label": "text", "block_order": 1, "block_content": "p", "block_bbox": [100, 100, 200, 200]},
    ]
    blocks = build_page_blocks(md, prl, {}, 0, _Idc(), None, False, [0], 595.0, 842.0, 1000.0, 1000.0)
    b = blocks[0].provenance.bbox
    expected = [165 * 595 / 1000, 234 * 842 / 1000, 787 * 595 / 1000, 296 * 842 / 1000]
    for got, exp in zip(b, expected):
        assert abs(got - exp) < 1e-6, (got, exp)
    # 不传页面尺寸 → 不换算（原样）
    blocks_raw = build_page_blocks(md, prl, {}, 0, _Idc(), None, False, [0])
    assert blocks_raw[0].provenance.bbox == [165, 234, 787, 296]


def test_list_alignment_prl_driven():
    """方案 B（issues5）：markdown 列表合并 + 页码泄漏不得破坏 prl 对齐。

    真实服务 page-3 形状：10 个内容块（含 7 个连续 "N. " 编号项）+ 1 个 number 页码。
    旧索引对齐下 markdown 只解析出 5 元素（列表合并成 1 + 页码 "3" 成 paragraph），
    页码 paragraph 拿到 text 的 bbox，DROP 永不触发。
    """
    md = (
        "## 前言\n\n本规程是在总结……编制完成的。\n\n本规程共分11章……主要内容如下：\n\n"
        "1. 明确了本规程的编制目的、适用范围。\n\n2. 规定了受下穿工程影响的控制标准。\n\n"
        "3. 提出了安全距离及防护措施要求。\n\n4. 明确了最小间距。\n\n5. 提出了选用原则。\n\n"
        "6. 提出了隧道下穿的要求。\n\n7. 明确了河道断面形式。\n\n3\n"
    )
    prl = [
        {"block_label": "paragraph_title", "block_order": 1, "block_content": "## 前言", "block_bbox": [515, 279, 684, 329]},
        {"block_label": "text", "block_order": 2, "block_content": "本规程是在总结……编制完成的。", "block_bbox": [168, 376, 1033, 567]},
        {"block_label": "text", "block_order": 3, "block_content": "本规程共分11章……主要内容如下：", "block_bbox": [172, 574, 1035, 715]},
        {"block_label": "text", "block_order": 4, "block_content": "1. 明确了本规程的编制目的、适用范围。", "block_bbox": [173, 722, 1034, 813]},
        {"block_label": "text", "block_order": 5, "block_content": "2. 规定了受下穿工程影响的控制标准。", "block_bbox": [173, 820, 1034, 913]},
        {"block_label": "text", "block_order": 6, "block_content": "3. 提出了安全距离及防护措施要求。", "block_bbox": [175, 920, 1036, 1012]},
        {"block_label": "text", "block_order": 7, "block_content": "4. 明确了最小间距。", "block_bbox": [176, 1019, 1037, 1111]},
        {"block_label": "text", "block_order": 8, "block_content": "5. 提出了选用原则。", "block_bbox": [178, 1116, 1036, 1208]},
        {"block_label": "text", "block_order": 9, "block_content": "6. 提出了隧道下穿的要求。", "block_bbox": [177, 1215, 1040, 1355]},
        {"block_label": "text", "block_order": 10, "block_content": "7. 明确了河道断面形式。", "block_bbox": [179, 1361, 1040, 1453]},
        {"block_label": "number", "block_order": 11, "block_content": "3", "block_bbox": [997, 1460, 1018, 1488]},
    ]
    blocks = build_page_blocks(md, prl, {}, 0, _Idc(), None, False, [0])
    texts = [b.text for b in blocks if isinstance(b, ParagraphNode)]
    # 页码 "3" 不得成块（旧索引对齐下它会以 paragraph 身份拿到 text bbox 存活）
    assert "3" not in texts, f"页码泄漏成 paragraph: {texts}"
    assert "学兔兔" not in texts
    # 结构：heading + 2 paragraph + list(7 items)
    assert isinstance(blocks[0], HeadingNode) and blocks[0].level == 2 and blocks[0].text == "前言"
    lists = [b for b in blocks if isinstance(b, ListNode)]
    assert len(lists) == 1, [type(b).__name__ for b in blocks]
    assert [it.blocks[0].text for it in lists[0].items] == [
        "1. 明确了本规程的编制目的、适用范围。", "2. 规定了受下穿工程影响的控制标准。",
        "3. 提出了安全距离及防护措施要求。", "4. 明确了最小间距。", "5. 提出了选用原则。",
        "6. 提出了隧道下穿的要求。", "7. 明确了河道断面形式。",
    ]
    # list 的 bbox = 成员并集（不是某一项的 bbox）
    assert lists[0].provenance.bbox == [173, 722, 1040, 1453]
    # 每个非列表块拿到自己的 bbox
    assert blocks[1].provenance.bbox == [168, 376, 1033, 567]
    assert blocks[0].provenance.bbox == [515, 279, 684, 329]


def test_header_offset_prl_driven():
    """真实 page-4 形状：header 打头 + 编号列表跨 8/9 + 页码结尾，全对齐。"""
    prl = [
        {"block_label": "header", "block_order": 1, "block_content": "学兔兔 www.bzfxw.com", "block_bbox": [24, 17, 270, 47]},
        {"block_label": "paragraph_title", "block_order": 2, "block_content": "## 前言", "block_bbox": [515, 279, 684, 329]},
        {"block_label": "text", "block_order": 3, "block_content": "8. 提出了监测技术要求。", "block_bbox": [173, 722, 1034, 813]},
        {"block_label": "text", "block_order": 4, "block_content": "9. 明确了实施日期。", "block_bbox": [173, 820, 1034, 913]},
        {"block_label": "text", "block_order": 5, "block_content": "主要审查人：xxx。", "block_bbox": [173, 920, 1036, 1012]},
        {"block_label": "number", "block_order": 6, "block_content": "4", "block_bbox": [997, 1460, 1018, 1488]},
    ]
    blocks = build_page_blocks("", prl, {}, 0, _Idc(), None, False, [0])
    all_texts = [getattr(b, "text", "") for b in blocks]
    assert "学兔兔 www.bzfxw.com" not in all_texts  # header 丢弃
    assert "4" not in [t for t in all_texts if t == "4"]  # 页码丢弃
    lists = [b for b in blocks if isinstance(b, ListNode)]
    assert len(lists) == 1 and len(lists[0].items) == 2
    paras = [b for b in blocks if isinstance(b, ParagraphNode)]
    assert paras[0].text == "主要审查人：xxx。" and paras[0].provenance.bbox == [173, 920, 1036, 1012]


def test_title_labels_atx_level():
    """doc_title/paragraph_title 的 block_content 自带 ATX 前缀，层级从前缀解析。"""
    prl = [
        {"block_label": "doc_title", "block_order": 1, "block_content": "# 公路与市政工程下穿高速铁路技术规程", "block_bbox": [80, 510, 1047, 578]},
        {"block_label": "paragraph_title", "block_order": 2, "block_content": "## 中华人民共和国行业标准", "block_bbox": [367, 211, 822, 265]},
        {"block_label": "paragraph_title", "block_order": 3, "block_content": "1 总则", "block_bbox": [10, 30, 100, 50]},
    ]
    blocks = build_page_blocks("", prl, {}, 0, _Idc(), None, False, [0])
    assert [b.level for b in blocks] == [1, 2, 1]
    assert [b.text for b in blocks] == ["公路与市政工程下穿高速铁路技术规程", "中华人民共和国行业标准", "1 总则"]


def test_header_image_dropped():
    """header_image（页眉装饰图）不产块——真实数据实测 label，旧 DROP_LABELS 漏掉。"""
    prl = [
        {"block_label": "header_image", "block_order": 1, "block_content": '<div><img src="imgs/a.jpg" alt="Image"/></div>', "block_bbox": [51, 1, 1147, 339]},
        {"block_label": "text", "block_order": 2, "block_content": "正文。", "block_bbox": [10, 400, 100, 420]},
    ]
    blocks = build_page_blocks("", prl, {}, 0, _Idc(), None, False, [0])
    assert len(blocks) == 1 and isinstance(blocks[0], ParagraphNode) and blocks[0].text == "正文。"


def test_chart_and_figure_title_labels():
    """chart → ImageNode（ref 从 block_content 的 <img src> 提取）；figure_title 剥 div → ParagraphNode。"""
    images = {"imgs/img_in_chart_box_1.jpg": TINY_PNG_B64}
    prl = [
        {"block_label": "chart", "block_order": 1, "block_content": '<div style="text-align: center;"><img src="imgs/img_in_chart_box_1.jpg" alt="Image" width="35%" /></div>\n', "block_bbox": [165, 357, 583, 697]},
        {"block_label": "figure_title", "block_order": 2, "block_content": '<div style="text-align: center;">表3.0.3 墩台顶位移限值(mm)</div>\n', "block_bbox": [416, 819, 796, 852]},
        {"block_label": "vision_footnote", "block_order": 3, "block_content": "注：1. 高低和轨向偏差为10 m及以下弦测量的最大矢度值。", "block_bbox": [204, 915, 810, 944]},
    ]
    blocks = build_page_blocks("", prl, images, 0, _Idc(), None, False, [0])
    assert isinstance(blocks[0], ImageNode) and blocks[0].format == "jpg"
    assert blocks[0].metadata.get("source_ref") == "imgs/img_in_chart_box_1.jpg"
    assert isinstance(blocks[1], ParagraphNode) and blocks[1].text == "表3.0.3 墩台顶位移限值(mm)"
    assert isinstance(blocks[2], ParagraphNode) and blocks[2].text.startswith("注：")


def test_empty_prl_falls_back_to_markdown():
    """prl 缺失（服务变体）→ 退回 markdown 建结构（bbox None），文本不丢。"""
    blocks = build_page_blocks(MD, [], {}, 0, _Idc(), None, False, [0])
    assert len(blocks) == 4
    assert isinstance(blocks[0], HeadingNode) and blocks[0].text == "标题一"
    assert all(b.provenance.bbox is None for b in blocks)


def test_heading_calibration_doc_level():
    """文档级标题定级（designs/004）：编号优先 + 高度聚类 + 大标题抽 metadata。"""
    from document2chunk.extractors.ocr._heading_level import calibrate
    from document2chunk.ir import DocumentMetadata, Provenance, SourceType

    def P(text, h=22):
        return ParagraphNode(id=f"p{text}", text=text,
                             provenance=Provenance(source_type=SourceType.OCR, bbox=[0, 0, 100, h]))

    def H(text, h, level):
        return HeadingNode(id=f"h{text}", level=level, text=text,
                           provenance=Provenance(source_type=SourceType.OCR, bbox=[0, 0, 100, h]))

    content = [
        H("国土资源部文件", 62, 1),                              # 大标题(版头)
        H("关于改进管理方式切实落实耕地占补平衡的通知", 74, 2),  # 大标题(真标题)
        H("一、A", 23, 2), P("正文1"),                           # cn_major（噪声 H2）
        H("二、B", 24, 1), P("正文2"),                           # cn_major（噪声 H1）
        H("三、C", 23, 1),                                      # cn_major
        H("四、D", 24, 2), P("正文3"),                           # cn_major（噪声 H2）
        H("（一）子项", 24, 3), P("正文4"),                      # cn_minor → H2
    ]
    md = DocumentMetadata(source_type=SourceType.OCR)
    out = calibrate(content, md)

    heads = [(b.level, b.text) for b in out if isinstance(b, HeadingNode)]
    # 决策 C：大标题=H1 + metadata.title；编号层级 +1（一、→H2,（一）→H3）
    assert heads == [(1, "关于改进管理方式切实落实耕地占补平衡的通知"),
                     (2, "一、A"), (2, "二、B"), (2, "三、C"), (2, "四、D"),
                     (3, "（一）子项")], heads
    # 大标题 → metadata.title（最长）；版头 → metadata.custom（降级 Paragraph，不丢）
    assert md.title == "关于改进管理方式切实落实耕地占补平衡的通知"
    assert md.custom.get("doc_titles") == ["国土资源部文件"]
    # 版头降级为 ParagraphNode（文本不丢）
    para_texts = [b.text for b in out if isinstance(b, ParagraphNode)]
    assert "国土资源部文件" in para_texts


if __name__ == "__main__":
    for fn in [
        test_parse_markdown_elements,
        test_build_page_blocks_types_and_provenance,
        test_table_html_to_node,
        test_image_saved_to_dir,
        test_drop_page_number_label,
        test_extractor_with_mock,
        test_multipage_pdf_chunking,
        test_block_and_inline_formulas,
        test_bbox_calibration_to_page_coords,
        test_heading_calibration_doc_level,
    ]:
        fn()
        print("ok:", fn.__name__)
    print("ALL OCR TESTS PASSED")
