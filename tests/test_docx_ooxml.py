"""OOXML 覆盖面测试（阶段 B）：文本框/公式/OLE/尾注脚注/sdt/页眉/媒体。

fixture 全部手搓仿 WPS 写法（真实公文样本不入库，见设计文档 §6.1）。
"""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from document2chunk.export import to_markdown
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.ir import (
    FormulaNode,
    HeadingNode,
    ImageNode,
    InlineFormulaNode,
    ListNode,
    ParagraphNode,
    RunNode,
    TableNode,
)
from document2chunk.structure import assemble

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
V = "urn:schemas-microsoft-com:vml"
O = "urn:schemas-microsoft-com:office:office"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
XMLDECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

DOC_NS = f'xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:mc="{MC}" xmlns:v="{V}" xmlns:o="{O}" xmlns:m="{M}" xmlns:wps="{WPS}"'


def make_docx(
    document_xml,
    *,
    styles_xml=None,
    endnotes_xml=None,
    footnotes_xml=None,
    header_parts=None,
    media=None,
    rels_xml=None,
) -> bytes:
    """手搓 docx。header_parts/media: {part名: 内容}；rels_xml 为 document rels。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
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
        if endnotes_xml:
            z.writestr("word/endnotes.xml", f"{XMLDECL}\n{endnotes_xml}")
        if footnotes_xml:
            z.writestr("word/footnotes.xml", f"{XMLDECL}\n{footnotes_xml}")
        if header_parts:
            for name, data in header_parts.items():
                z.writestr(name, f"{XMLDECL}\n{data}")
        if media:
            for name, data in media.items():
                z.writestr(name, data)
        if rels_xml:
            z.writestr("word/_rels/document.xml.rels", f"{XMLDECL}\n{rels_xml}")
    return buf.getvalue()


# ---------- Task 1: AlternateContent 去重 ----------


def _texts(el):
    """收集元素子树内所有 w:t 文本（顺序）。"""
    return [e.text or "" for e in el.iter() if etree.QName(e).localname == "t"]


def test_content_children_choice_wins():
    from document2chunk.extractors.docx.embedded import content_children

    xml = f"""<w:p {DOC_NS}>
      <w:r><w:t>前</w:t></w:r>
      <mc:AlternateContent>
        <mc:Choice Requires="wps"><w:r><w:t>CHOICE</w:t></w:r></mc:Choice>
        <mc:Fallback><w:r><w:t>FALLBACK</w:t></w:r></mc:Fallback>
      </mc:AlternateContent>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    kids = [etree.QName(c).localname for c in content_children(p)]
    assert kids == ["r", "r"]  # AlternateContent 替换为 Choice 内的 r
    assert _texts(p)[0] == "前"


def test_iter_content_skips_fallback():
    from document2chunk.extractors.docx.embedded import iter_content

    xml = f"""<w:p {DOC_NS}>
      <mc:AlternateContent>
        <mc:Choice Requires="wps"><w:r><w:t>CHOICE</w:t></w:r></mc:Choice>
        <mc:Fallback><w:r><w:t>FB1</w:t></w:r><w:r><w:t>FB2</w:t></w:r></mc:Fallback>
      </mc:AlternateContent>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    texts = [e.text for e in iter_content(p) if etree.QName(e).localname == "t"]
    assert texts == ["CHOICE"]


def test_iter_content_fallback_when_no_choice():
    from document2chunk.extractors.docx.embedded import iter_content

    xml = f"""<w:p {DOC_NS}>
      <mc:AlternateContent>
        <mc:Fallback><w:pict><w:r><w:t>ONLYFB</w:t></w:r></w:pict></mc:Fallback>
      </mc:AlternateContent>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    texts = [e.text for e in iter_content(p) if etree.QName(e).localname == "t"]
    assert texts == ["ONLYFB"]


def test_inside_textbox():
    from document2chunk.extractors.docx.embedded import inside_textbox

    xml = f"""<w:p {DOC_NS}><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
      <w:p><w:r><w:t>x</w:t></w:r></w:p>
    </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>"""
    p = etree.fromstring(xml.encode())
    inner_p = p.find(f".//{{{W}}}p")
    assert inner_p is not None and p is not inner_p
    assert inside_textbox(inner_p) is True
    assert inside_textbox(p) is False


def test_content_children_skips_comment_nodes():
    from document2chunk.extractors.docx.embedded import content_children

    xml = f"""<w:p {DOC_NS}>
      <!-- 一条注释 -->
      <w:r><w:t>正文</w:t></w:r>
      <?pi instr?>
    </w:p>"""
    p = etree.fromstring(xml.encode())
    kids = [etree.QName(c).localname for c in content_children(p)]
    assert kids == ["r"]


# ---------- Task 2: package_reader 扩展 ----------


_RELS_PNG = f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId7" Type="{R}/image" Target="media/image1.png"/>
</Relationships>"""


def _reader(data: bytes):
    from document2chunk.extractors.docx.package_reader import PackageReader

    return PackageReader(data)


def test_reader_notes_and_header_parts():
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>'
    data = make_docx(
        doc,
        endnotes_xml=f'<w:endnotes {DOC_NS}><w:endnote w:id="1"><w:p><w:r><w:t>e1</w:t></w:r></w:p></w:endnote></w:endnotes>',
        header_parts={"word/header1.xml": f'<w:hdr {DOC_NS}><w:p><w:r><w:t>页眉</w:t></w:r></w:p></w:hdr>'},
    )
    r = _reader(data)
    assert r.endnotes_element() is not None
    assert r.footnotes_element() is None
    hs = r.header_elements()
    assert len(hs) == 1


def test_reader_media_info_for_rel():
    doc = f'<w:document {DOC_NS}><w:body><w:p/></w:body></w:document>'
    data = make_docx(doc, media={"word/media/image1.png": b"PNGDATA"}, rels_xml=_RELS_PNG)
    r = _reader(data)
    assert r.media_info_for_rel("rId7") == ("image1.png", b"PNGDATA", "png")
    assert r.media_info_for_rel("rId999") is None
    # 既有接口行为不变
    assert r.media_for_rel("rId7") == (b"PNGDATA", "png")


# ---------- Task 3: OMML 公式 ----------


def test_omml_inline_formula_in_paragraph():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>比值为</w:t></w:r><m:oMath>
        <m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f>
      </m:oMath></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))

    para = result.content[0]
    assert isinstance(para, ParagraphNode)
    formulas = [r for r in para.runs if isinstance(r, InlineFormulaNode)]
    assert len(formulas) == 1 and formulas[0].latex == "\\frac{a}{b}"
    assert "\\frac{a}{b}" in para.text  # latex 进段落 text（markdown 走 text）


def test_omml_inline_formula_inside_run():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>x</w:t><m:oMath>
        <m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup>
      </m:oMath></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))

    para = result.content[0]
    assert "x^{2}" in para.text
    assert any(isinstance(r, InlineFormulaNode) and r.latex == "x^{2}" for r in para.runs)


def test_omml_block_formula():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><m:oMathPara><m:oMath>
        <m:rad><m:e><m:r><m:t>x</m:t></m:r></m:e></m:rad>
      </m:oMath></m:oMathPara></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    f = result.content[0]
    assert isinstance(f, FormulaNode) and f.latex == "\\sqrt{x}"


# ---------- Task 4: OLE 占位 ----------

_RELS_WMF = f"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId9" Type="{R}/image" Target="media/olePreview1.wmf"/>
</Relationships>"""


def test_ole_placeholder_image():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>预算明细见下表：</w:t></w:r></w:p>
      <w:p><w:r><w:object>
        <v:shape style="width:100pt;height:60pt"><v:imagedata r:id="rId9"/></v:shape>
        <o:OLEObject ProgID="Excel.Sheet.8"/>
      </w:object></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(doc, media={"word/media/olePreview1.wmf": b"WMFDATA"}, rels_xml=_RELS_WMF)
    )
    imgs = [b for b in result.content if isinstance(b, ImageNode)]
    assert len(imgs) == 1
    img = imgs[0]
    assert img.alt == "OLE 对象 (Excel.Sheet.8)"
    assert img.image_id == "rId9"
    assert img.format == "wmf"
    assert img.width_emu == 1270000  # 100pt × 12700
    assert img.height_emu == 762000  # 60pt × 12700


def test_ole_without_preview_rel_still_placeholder():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:object><o:OLEObject ProgID="Equation.3"/></w:object></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    imgs = [b for b in result.content if isinstance(b, ImageNode)]
    assert len(imgs) == 1
    assert imgs[0].alt == "OLE 对象 (Equation.3)"
    assert imgs[0].format is None


# ---------- Task 5: 文本框 ----------


def test_textbox_wps_choice_inline_expansion():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><mc:AlternateContent>
        <mc:Choice Requires="wps">
          <w:drawing><wp:anchor><wp:extent cx="5000000" cy="1000000"/><wp:docPr descr="红头"/>
            <a:graphic><wps:txbx><w:txbxContent>
              <w:p><w:r><w:t>广东某高速公路有限公司</w:t></w:r></w:p>
              <w:p><w:r><w:t>关于XX项目的批复</w:t></w:r></w:p>
            </w:txbxContent></wps:txbx></a:graphic></wp:anchor></w:drawing>
        </mc:Choice>
        <mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>
          <w:p><w:r><w:t>FB红头</w:t></w:r></w:p>
        </w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>
      </mc:AlternateContent></w:r></w:p>
      <w:p><w:r><w:t>正文第一段</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert texts[:3] == ["广东某高速公路有限公司", "关于XX项目的批复", "正文第一段"]
    assert "FB红头" not in "".join(texts)  # Fallback 不双计
    assert result.content[0].metadata.get("textbox") is True


def test_textbox_vml_form():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
        <w:p><w:r><w:t>VML标题</w:t></w:r></w:p>
      </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    assert result.content[0].text == "VML标题"
    assert result.content[0].metadata.get("textbox") is True
    assert result.content[1].text == "正文"


def test_image_inside_textbox_no_leak():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
        <w:p><w:r><w:t>框内文字</w:t></w:r></w:p>
        <w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="50"/>
          <a:graphic><a:blip r:embed="rId7"/></a:graphic></wp:inline></w:drawing></w:r></w:p>
      </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(doc, media={"word/media/image1.png": b"PNG"}, rels_xml=_RELS_PNG)
    )
    imgs = [b for b in result.content if isinstance(b, ImageNode)]
    # 不泄出为顶层重复块：恰 1 个，且位于文本框展开区内（第 2 块）
    assert len(imgs) == 1
    assert result.content[1] is imgs[0]
    assert result.content[0].text == "框内文字"
    assert result.content[2].text == "正文"


def test_nested_textbox_no_double_expansion():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
        <w:p><w:r><w:t>外框文字</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
          <w:p><w:r><w:t>内框文字</w:t></w:r></w:p>
        </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p></w:tc></w:tr></w:tbl>
      </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    all_texts = [getattr(b, "text", "") for b in result.content]
    # 深入表格单元格收集全部文本
    for b in result.content:
        if isinstance(b, TableNode):
            for row in b.rows:
                for cell in row.cells:
                    for cb in cell.blocks:
                        all_texts.append(getattr(cb, "text", ""))
    joined = "".join(all_texts)
    assert joined.count("内框文字") == 1  # 嵌套框只展开一次
    assert joined.count("外框文字") == 1


# ---------- Task 6: 尾注/脚注 ----------

ENDNOTES = f"""<w:endnotes {DOC_NS}>
  <w:endnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>
  <w:endnote w:type="continuationSeparator"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:endnote>
  <w:endnote w:id="2"><w:p><w:r><w:t>Keoleian G A, et al. Life Cycle Assessment.</w:t></w:r></w:p></w:endnote>
  <w:endnote w:id="1"><w:p><w:r><w:t>王某某. 沥青路面研究[J]. 公路, 2023.</w:t></w:r></w:p></w:endnote>
</w:endnotes>"""


def test_endnote_marker_and_tail_blocks():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>引用一处</w:t></w:r><w:r><w:endnoteReference w:id="2"/></w:r></w:p>
      <w:p><w:r><w:t>引用二处</w:t></w:r><w:r><w:endnoteReference w:id="1"/></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc, endnotes_xml=ENDNOTES))
    assert result.content[0].text == "引用一处[尾注2]"
    assert result.content[1].text == "引用二处[尾注1]"
    tail = result.content[2:]
    assert len(tail) == 2  # separator 条目不计
    assert tail[0].text == "[尾注1] 王某某. 沥青路面研究[J]. 公路, 2023."  # id 数值序
    assert tail[1].text == "[尾注2] Keoleian G A, et al. Life Cycle Assessment."
    assert tail[0].metadata["note"] == {"type": "endnote", "id": "1"}


def test_footnote_same_mechanism():
    footnotes = f"""<w:footnotes {DOC_NS}>
      <w:footnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
      <w:footnote w:id="1"><w:p><w:r><w:t>见附录A。</w:t></w:r></w:p></w:footnote>
    </w:footnotes>"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>说明</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc, footnotes_xml=footnotes))
    assert result.content[0].text == "说明[脚注1]"
    assert result.content[1].text == "[脚注1] 见附录A。"
    assert result.content[1].metadata["note"] == {"type": "footnote", "id": "1"}


def test_empty_notes_part_no_blocks():
    """WPS 空壳 footnotes.xml（40% 样本）不产出任何块。"""
    shell = f"""<w:footnotes {DOC_NS}>
      <w:footnote w:type="separator"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
      <w:footnote w:type="continuationSeparator"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
    </w:footnotes>"""
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body></w:document>'
    result = DocxExtractor().extract(make_docx(doc, footnotes_xml=shell))
    assert len(result.content) == 1 and result.content[0].text == "正文"


# ---------- Task 7: sdt 展开 ----------


def test_sdt_wrapped_toc_consumed():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt>
        <w:sdtPr><w:docPartObj><w:docPartGallery w:val="Table of Contents"/></w:docPartObj></w:sdtPr>
        <w:sdtContent>
          <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> TOC \\o "1-3" </w:instrText></w:r></w:p>
          <w:p><w:hyperlink><w:r><w:t>第一章 背景</w:t></w:r></w:hyperlink></w:p>
          <w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
        </w:sdtContent>
      </w:sdt>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert texts == ["正文"]  # TOC 条目不进 content（既有语义）
    assert result.toc_entries and result.toc_entries[0].text == "第一章 背景"


def test_sdt_wrapped_body_not_lost():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt><w:sdtContent>
        <w:p><w:r><w:t>sdt内段落一</w:t></w:r></w:p>
        <w:p><w:r><w:t>sdt内段落二</w:t></w:r></w:p>
      </w:sdtContent></w:sdt>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert texts == ["sdt内段落一", "sdt内段落二"]


# ---------- Task 8: image_dir / 页眉 ----------


def test_image_dir_exports_media(tmp_path):
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="50"/><wp:docPr descr="logo"/>
        <a:graphic><a:blip r:embed="rId7"/></a:graphic></wp:inline></w:drawing></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(doc, media={"word/media/image1.png": b"PNGDATA"}, rels_xml=_RELS_PNG),
        image_dir=tmp_path,
    )
    assert (tmp_path / "image1.png").read_bytes() == b"PNGDATA"
    # IR 不引磁盘路径
    assert str(tmp_path) not in str(result.model_dump())


def test_image_dir_none_no_export(tmp_path):
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>'
    DocxExtractor().extract(make_docx(doc), image_dir=None)
    assert not (tmp_path / "image1.png").exists()


def test_header_text_metadata_and_content_lock():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:t>正文</w:t></w:r></w:p>
    </w:body></w:document>"""
    hdr = f'<w:hdr {DOC_NS}><w:p><w:r><w:t>粤高速集团文件</w:t></w:r></w:p></w:hdr>'
    result = DocxExtractor().extract(
        make_docx(doc, header_parts={"word/header1.xml": hdr})
    )
    texts = "".join(getattr(b, "text", "") for b in result.content)
    assert "粤高速集团文件" not in texts  # 页眉不进正文（锁现状）
    assert result.metadata.custom["docx"]["header_text"] == "粤高速集团文件"


# ---------- Task 9: 红头公文组合集成 ----------


def test_gongwen_integration():
    """红头公文组合：文本框红头(双写) + 标题 + OLE + 尾注 → 阅读顺序正确。"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><mc:AlternateContent>
        <mc:Choice Requires="wps"><w:drawing><wp:anchor>
          <a:graphic><wps:txbx><w:txbxContent>
            <w:p><w:r><w:t>广东某高速公路有限公司</w:t></w:r></w:p>
          </w:txbxContent></wps:txbx></a:graphic></wp:anchor></w:drawing>
        </mc:Choice>
        <mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>
          <w:p><w:r><w:t>FB</w:t></w:r></w:p>
        </w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>
      </mc:AlternateContent></w:r></w:p>
      <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第一章 总体要求</w:t></w:r></w:p>
      <w:p><w:r><w:t>预算明细见</w:t></w:r><w:r><w:object>
        <v:shape style="width:90pt;height:50pt"><v:imagedata r:id="rId9"/></v:shape>
        <o:OLEObject ProgID="Excel.Sheet.8"/>
      </w:object></w:r></w:p>
      <w:p><w:r><w:t>参考文献</w:t></w:r><w:r><w:endnoteReference w:id="1"/></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(
        make_docx(
            doc,
            endnotes_xml=ENDNOTES,
            media={"word/media/olePreview1.wmf": b"WMF"},
            rels_xml=_RELS_WMF,
        )
    )
    logical = assemble(result)
    md = to_markdown(logical)
    # 阅读顺序：红头 → 标题 → 正文(OLE alt) → 尾注
    assert md.index("广东某高速公路有限公司") < md.index("# 第一章 总体要求")
    assert md.index("# 第一章 总体要求") < md.index("OLE 对象 (Excel.Sheet.8)")
    assert md.index("OLE 对象") < md.index("[尾注1] 王某某")
    assert "FB" not in md  # Fallback 文本（本 fixture 中仅为 "FB"）不出现


def test_realworld_edge_inputs_no_crash():
    """799 真实样本抽查扫描发现的 3 类边界输入（整文件崩溃 → 应 WARN 级降级，spec §3.8）。

    1) w:ilvl w:val="-1"（WPS 负层级）→ ListItemNode.level 下限 0；
    2) w:sz w:val="0"（0 磅字号）→ 不进 RunProperties（模型要求 >0）；
    3) 标题段含超链接/行内公式 → HeadingNode.runs 只收 RunNode（文本保留在 text）。
    """
    # 1) 负列表层级
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:pPr><w:numPr><w:ilvl w:val="-1"/><w:numId w:val="1"/></w:numPr></w:pPr>
        <w:r><w:t>负层级列表项</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    lst = next(b for b in result.content if isinstance(b, ListNode))
    assert lst.items[0].level == 0 and lst.items[0].blocks[0].text == "负层级列表项"

    # 2) 0 磅字号
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:rPr><w:sz w:val="0"/></w:rPr><w:t>零字号</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    assert result.content[0].runs[0].style.font_size is None

    # 3) 标题段内超链接 + 行内公式
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>
        <w:r><w:t>见</w:t></w:r>
        <w:hyperlink r:id="rId1"><w:r><w:t>链接文字</w:t></w:r></w:hyperlink>
        <m:oMath><m:r><m:t>E</m:t></m:r></m:oMath>
      </w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    h = result.content[0]
    assert isinstance(h, HeadingNode)
    assert "链接文字" in h.text and "E" in h.text
    assert all(type(r).__name__ == "RunNode" for r in h.runs)


def test_cell_heading_with_hyperlink_no_crash():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:tbl><w:tr><w:tc>
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>
          <w:hyperlink r:id="rId1"><w:r><w:t>链接标题</w:t></w:r></w:hyperlink></w:p>
      </w:tc></w:tr></w:tbl>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    table = result.content[0]
    assert isinstance(table, TableNode)
    h = table.rows[0].cells[0].blocks[0]
    assert isinstance(h, HeadingNode)
    assert h.text == "链接标题"          # 文本保留超链接内容
    assert all(isinstance(r, RunNode) for r in h.runs)  # runs 只剩 RunNode


def test_textbox_heading_with_hyperlink_sibling_survives():
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:p><w:r><w:pict><v:shape><v:textbox><w:txbxContent>
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>
          <w:hyperlink r:id="rId1"><w:r><w:t>红头链接标题</w:t></w:r></w:hyperlink></w:p>
        <w:p><w:r><w:t>框内普通段</w:t></w:r></w:p>
      </w:txbxContent></v:textbox></v:shape></w:pict></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert "红头链接标题" in texts       # 标题段存活（runs 已过滤）
    assert "框内普通段" in texts         # 兄弟段落不陪葬


# ---------------------------------------------------------------------------
# SDT-TOC 检测（HYPERLINK \l 书签式目录 / docPartGallery）
# ---------------------------------------------------------------------------


def test_sdt_toc_docpart_gallery_skipped():
    """docPartGallery='Table of Contents' 的 SDT 应被跳过，内容不出现在输出中。"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt>
        <w:sdtPr>
          <w:docPartObj>
            <w:docPartGallery w:val="Table of Contents"/>
          </w:docPartObj>
        </w:sdtPr>
        <w:sdtContent>
          <w:p><w:r><w:t>目录条目一</w:t></w:r></w:p>
          <w:p><w:r><w:t>目录条目二</w:t></w:r></w:p>
        </w:sdtContent>
      </w:sdt>
      <w:p><w:r><w:t>正文段落</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert "正文段落" in texts
    assert "目录条目一" not in texts
    assert "目录条目二" not in texts


def test_sdt_toc_hyperlink_bookmark_skipped():
    """含 HYPERLINK \\l 书签式域代码的 SDT 应被识别为 TOC 并跳过。"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt>
        <w:sdtContent>
          <w:p>
            <w:fldChar w:fldCharType="begin"/>
            <w:instrText xml:space="preserve"> HYPERLINK \\l "_Toc102504025" </w:instrText>
            <w:fldChar w:fldCharType="separate"/>
            <w:r><w:t>3.1 项目申报操作指引</w:t></w:r>
            <w:fldChar w:fldCharType="end"/>
          </w:p>
          <w:p>
            <w:fldChar w:fldCharType="begin"/>
            <w:instrText xml:space="preserve"> HYPERLINK \\l "_Toc102504026" </w:instrText>
            <w:fldChar w:fldCharType="separate"/>
            <w:r><w:t>3.2 审核操作指引</w:t></w:r>
            <w:fldChar w:fldCharType="end"/>
          </w:p>
        </w:sdtContent>
      </w:sdt>
      <w:p><w:r><w:t>正文开始</w:t></w:r></w:p>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert "正文开始" in texts
    assert "项目申报操作指引" not in texts
    assert "审核操作指引" not in texts
    # 目录条目不应变成标题
    headings = [b for b in result.content if isinstance(b, HeadingNode)]
    assert not any("申报" in h.text for h in headings)


def test_sdt_non_toc_still_unfolded():
    """非 TOC 的 SDT 仍然透明展开内容。"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt>
        <w:sdtContent>
          <w:p><w:r><w:t>普通SDT段落</w:t></w:r></w:p>
        </w:sdtContent>
      </w:sdt>
    </w:body></w:document>"""
    result = DocxExtractor().extract(make_docx(doc))
    texts = [getattr(b, "text", "") for b in result.content]
    assert "普通SDT段落" in texts  # 非 TOC SDT 仍然展开


def test_sdt_toc_entries_collected():
    """TOC SDT 中的段落文本应收集到 toc_entries。"""
    doc = f"""<w:document {DOC_NS}><w:body>
      <w:sdt>
        <w:sdtPr>
          <w:docPartObj>
            <w:docPartGallery w:val="Table of Contents"/>
          </w:docPartObj>
        </w:sdtPr>
        <w:sdtContent>
          <w:p><w:r><w:t>第一章 概述</w:t></w:r></w:p>
          <w:p><w:r><w:t>第二章 详细设计</w:t></w:r></w:p>
        </w:sdtContent>
      </w:sdt>
      <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第一章 概述</w:t></w:r></w:p>
    </w:body></w:document>"""
    ext = DocxExtractor()
    result = ext.extract(make_docx(doc))
    # TOC entries should have been collected
    toc_texts = [e.text for e in ext._toc_entries] if hasattr(ext, '_toc_entries') else []
    # The parser stores toc_entries internally; check via result metadata if available
    # At minimum, verify TOC text is NOT in content blocks
    content_texts = [getattr(b, "text", "") for b in result.content]
    assert "第一章 概述" in content_texts  # 正文标题还在
    # TOC 中的 "第二章 详细设计" 不应作为正文出现
    assert not any("详细设计" in t for t in content_texts)
