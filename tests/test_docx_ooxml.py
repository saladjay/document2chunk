"""OOXML 覆盖面测试（阶段 B）：文本框/公式/OLE/尾注脚注/sdt/页眉/媒体。

fixture 全部手搓仿 WPS 写法（真实公文样本不入库，见设计文档 §6.1）。
"""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from document2chunk.extractors.docx import DocxExtractor

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
