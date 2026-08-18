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
    # H1, H(doc_title提升), H(pStyle), Table, List
    assert len(result.content) == 5
    assert isinstance(result.content[0], HeadingNode)
    assert isinstance(result.content[3], TableNode)
    assert isinstance(result.content[4], ListNode)


def test_heading_levels():
    result = DocxExtractor().extract(_doc())
    assert result.content[0].level == 2  # outlineLvl 0，因 doc_title 提升至 H2
    assert result.content[0].text == "第一章"
    assert result.content[2].level == 2  # pStyle Heading1 经继承链，因 doc_title 提升至 H2
    assert result.content[2].text == "标题"


def test_run_style_resolved():
    result = DocxExtractor().extract(_doc())
    # content[1]「粗体14pt」被提升为 doc_title HeadingNode，runs 仍保留 run 样式
    heading = result.content[1]
    run = heading.runs[0]
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
    lines = md.splitlines()
    assert "## 第一章" in lines
    assert "# 粗体14pt" in lines   # 提升为 doc_title → H1
    assert "## 标题" in lines
    assert "| A | B |" in md
    assert "- 项一" in md


DOC_MARKED = f"""<w:document xmlns:w="{W}">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>总体要求</w:t></w:r></w:p>
    <w:p><w:r><w:t>一、指导思想</w:t></w:r></w:p>
    <w:p><w:r><w:t>二、基本原则。这里是正文不是标题因为句号结尾。</w:t></w:r></w:p>
    <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:t>某某文件标题</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def test_heading_source_markers():
    result = DocxExtractor().extract(make_docx(DOC_MARKED, STYLES))
    h = result.content[0]
    assert isinstance(h, HeadingNode)
    assert h.metadata.get("heading_source") == "style"


def test_pseudo_heading_promotion():
    """无样式短编号段落 → heading_source=heuristic 的 HeadingNode；
    句号结尾的长段不提升。"""
    result = DocxExtractor().extract(make_docx(DOC_MARKED, STYLES))
    assert isinstance(result.content[1], HeadingNode)
    assert result.content[1].metadata.get("heading_source") == "heuristic"
    assert result.content[1].text == "一、指导思想"
    # 句号结尾 → 仍是段落
    assert isinstance(result.content[2], ParagraphNode)


def test_centered_marker():
    result = DocxExtractor().extract(make_docx(DOC_MARKED, STYLES))
    p = result.content[3]
    # 居中且字号比 22/11=2.0≥1.0 的段落被提升为 doc_title（HeadingNode level 1）
    assert isinstance(p, HeadingNode)
    assert p.metadata.get("centered") is True
    assert p.level == 1  # 主标题
    assert p.runs[0].style.font_size == 22.0


DOC_FULL = f"""<w:document xmlns:w="{W}">
  <w:body>
    <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:sz w:val="44"/></w:rPr><w:t>关于加强耕地保护提升耕地质量的通知</w:t></w:r></w:p>
    <w:p><w:r><w:t>一、总体要求</w:t></w:r></w:p>
    <w:p><w:r><w:t>坚持最严格的耕地保护制度。</w:t></w:r></w:p>
    <w:p><w:r><w:t>二、重点工作</w:t></w:r></w:p>
    <w:p><w:r><w:t>附件1：占补平衡指标表</w:t></w:r></w:p>
    <w:p><w:r><w:t>附件说明正文。</w:t></w:r></w:p>
  </w:body>
</w:document>"""


def test_extractor_postprocess_wired():
    """extract() 走 postprocess：doc_title 提升 + 伪标题定级 + 附件拆分。"""
    result = DocxExtractor().extract(make_docx(DOC_FULL, STYLES))
    # doc_title：居中 22pt（正文 11pt，比≈2.0 ≥1.2）→ metadata.title
    assert result.metadata.title == "关于加强耕地保护提升耕地质量的通知"
    # 附件拆分
    assert len(result.attachments) == 1
    assert result.attachments[0].metadata.custom.get("is_attachment") is True
    # 主文标题层级：doc_title H1；一、/二、 伪标题 → H2
    levels = [(b.level, b.text) for b in result.content if isinstance(b, HeadingNode)]
    assert (2, "一、总体要求") in levels
    assert (2, "二、重点工作") in levels


def test_debug_dir_postprocess_log(tmp_path):
    """options.debug_dir 时落 postprocess_log.json（spec §3，pdf.py 同模式）。"""
    result = DocxExtractor().extract(
        make_docx(DOC_FULL, STYLES), options={"debug_dir": str(tmp_path)}
    )
    import json
    log_file = tmp_path / "postprocess_log.json"
    assert log_file.exists()
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and entries  # 非空：calibrate 等决策有记录


def test_extractor_regression_basic():
    """既有 _doc() 样例接线后结构不变（5 块、层级因 doc_title 提升而调整）。"""
    result = DocxExtractor().extract(_doc())
    assert len(result.content) == 5
    # "粗体14pt" 段落因字号比 14/11=1.27≥1.2 被提升为 doc_title，导致原有标题层级+1
    assert result.content[0].level == 2  # 原 H1 → H2（因 doc_title 存在）
    assert result.content[2].level == 2  # 原 H1 → H2（因 doc_title 存在）
    assert result.attachments == []


def test_extractor_split_tables_merged():
    """连续同表头表格合并（DOCX 手工拆分表场景）。"""
    tbl = """<w:tbl><w:tr><w:tc><w:p><w:r><w:t>序号</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>名称</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>甲</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"""
    document = f'<w:document xmlns:w="{W}"><w:body>{tbl}{tbl}<w:p><w:r><w:t>正文。</w:t></w:r></w:p></w:body></w:document>'
    result = DocxExtractor().extract(make_docx(document, STYLES))
    tables = [b for b in result.content if isinstance(b, TableNode)]
    assert len(tables) == 1
    assert len(tables[0].rows) == 3  # 首表表头 + 数据行1 + 数据行2（第二表跳过表头追加）


if __name__ == "__main__":
    for fn in [
        test_extract_basic,
        test_heading_levels,
        test_run_style_resolved,
        test_provenance_none,
        test_table_cells,
        test_list_grouping,
        test_assemble_and_markdown,
        test_heading_source_markers,
        test_pseudo_heading_promotion,
        test_centered_marker,
        test_extractor_postprocess_wired,
        test_extractor_regression_basic,
        test_extractor_split_tables_merged,
    ]:
        fn()
        print(f"ok: {fn.__name__}")
    print("ALL DOCX TESTS PASSED")
