"""docx-extractor 测试（手搓 minimal docx，无需 python-docx）。"""

from __future__ import annotations

import io
import zipfile

from document2chunk.export import to_markdown
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.ir import (
    HeadingNode,
    ListNode,
    ParagraphNode,
    SourceType,
    TableNode,
)
from document2chunk.structure import assemble

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XMLDECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def make_docx(document_xml, styles_xml=None, numbering_xml=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="text/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            "</Types>",
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        z.writestr("word/document.xml", f"{XMLDECL}\n{document_xml}")
        if styles_xml:
            z.writestr("word/styles.xml", f"{XMLDECL}\n{styles_xml}")
        if numbering_xml:
            z.writestr("word/numbering.xml", f"{XMLDECL}\n{numbering_xml}")
    return buf.getvalue()


STYLES = f"""<w:styles xmlns:w="{W}">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
    <w:pPr><w:outlineLvl w:val="0"/></w:pPr>
  </w:style>
</w:styles>"""

NUMBERING = f"""<w:numbering xmlns:w="{W}">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>"""


def _doc():
    document = f"""<w:document xmlns:w="{W}">
  <w:body>
    <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第一章</w:t></w:r></w:p>
    <w:p><w:r><w:rPr><w:b/><w:sz w:val="28"/></w:rPr><w:t>粗体14pt</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>标题</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>项一</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>项二</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    return make_docx(document, STYLES, NUMBERING)


def test_extract_basic():
    result = DocxExtractor().extract(_doc())
    assert result.metadata.source_type == SourceType.DOCX
    # H1, P, H1(pStyle), Table, List
    assert len(result.content) == 5
    assert isinstance(result.content[0], HeadingNode)
    assert isinstance(result.content[3], TableNode)
    assert isinstance(result.content[4], ListNode)


def test_heading_levels():
    result = DocxExtractor().extract(_doc())
    assert result.content[0].level == 1  # outlineLvl 0
    assert result.content[0].text == "第一章"
    assert result.content[2].level == 1  # pStyle Heading1 经继承链
    assert result.content[2].text == "标题"


def test_run_style_resolved():
    result = DocxExtractor().extract(_doc())
    para: ParagraphNode = result.content[1]
    run = para.runs[0]
    assert run.text == "粗体14pt"
    assert run.style.bold is True
    assert run.style.font_size == 14.0  # sz val=28 → 14pt
    assert run.style.font == "Calibri"  # 继承自 docDefaults


def test_provenance_none():
    result = DocxExtractor().extract(_doc())
    for b in result.content:
        assert b.provenance is None


def test_table_cells():
    result = DocxExtractor().extract(_doc())
    table: TableNode = result.content[3]
    assert len(table.rows) == 2
    assert len(table.rows[0].cells) == 2
    # 单元格内段落文本
    cell_a = table.rows[0].cells[0].blocks[0]
    assert cell_a.text == "A"


def test_list_grouping():
    result = DocxExtractor().extract(_doc())
    lst: ListNode = result.content[4]
    assert len(lst.items) == 2
    assert lst.items[0].blocks[0].text == "项一"
    assert lst.items[1].blocks[0].text == "项二"
    assert lst.ordered is False  # numFmt bullet


def test_assemble_and_markdown():
    result = DocxExtractor().extract(_doc())
    doc = assemble(result)
    md = to_markdown(doc)
    assert "# 第一章" in md
    assert "# 标题" in md
    assert "| A | B |" in md
    assert "- 项一" in md


if __name__ == "__main__":
    for fn in [
        test_extract_basic,
        test_heading_levels,
        test_run_style_resolved,
        test_provenance_none,
        test_table_cells,
        test_list_grouping,
        test_assemble_and_markdown,
    ]:
        fn()
        print(f"ok: {fn.__name__}")
    print("ALL DOCX TESTS PASSED")
