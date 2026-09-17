"""round-1 等价提效改动的 TDD 测试(docs/大文件提效调研.md §5 序 2/4/7)。

约定:标 RED_* 的用例在改动前必须失败(新行为缺失),
     标 GUARD_* 的用例是特征化守卫(改前改后都必须通过)。
"""
from __future__ import annotations

import time

import pytest

from test_docx_ooxml import DOC_NS, XMLDECL, _RELS_PNG, make_docx
from test_postprocess import GEO, P


# ---------- 1. rels 解析缓存(package_reader) ----------

def _reader(data: bytes):
    from document2chunk.extractors.docx.package_reader import PackageReader
    return PackageReader(data)


def _count_read_xml(monkeypatch) -> dict:
    """包一层 PackageReader.read_xml,按 part 名计数。"""
    from document2chunk.extractors.docx.package_reader import PackageReader
    orig = PackageReader.read_xml
    counts: dict = {}

    def wrapped(self, name):
        counts[name] = counts.get(name, 0) + 1
        return orig(self, name)

    monkeypatch.setattr(PackageReader, "read_xml", wrapped)
    return counts


def test_red_media_info_for_rel_parses_rels_once(monkeypatch):
    doc = f'<w:document {DOC_NS}><w:body><w:p/></w:body></w:document>'
    data = make_docx(doc, media={"word/media/image1.png": b"PNGDATA"}, rels_xml=_RELS_PNG)
    counts = _count_read_xml(monkeypatch)
    r = _reader(data)
    rels_name = "word/_rels/document.xml.rels"

    r.media_info_for_rel("rId7")
    r.media_info_for_rel("rId7")
    r.media_info_for_rel("rId999")
    assert counts.get(rels_name, 0) == 1, f"rels 应只解析一次,实际 {counts.get(rels_name)} 次"


def test_red_rel_target_avoids_media_bytes(monkeypatch):
    """rel_target(rel_id) → (name, ext),绝不解压图片字节。"""
    from document2chunk.extractors.docx.package_reader import PackageReader
    doc = f'<w:document {DOC_NS}><w:body><w:p/></w:body></w:document>'
    data = make_docx(doc, media={"word/media/image1.png": b"PNGDATA"}, rels_xml=_RELS_PNG)
    orig = PackageReader.read_bytes
    media_reads: list = []

    def wrapped_bytes(self, name):
        media_reads.append(name)
        return orig(self, name)

    monkeypatch.setattr(PackageReader, "read_bytes", wrapped_bytes)
    r = _reader(data)

    assert r.rel_target("rId7") == ("image1.png", "png")
    assert r.rel_target("rId7") == ("image1.png", "png")
    assert r.rel_target("rId999") is None
    assert not any(n.startswith("word/media/") for n in media_reads), (
        f"rel_target 不应读媒体字节,实际读了 {media_reads}")


# ---------- 2. 样式继承链缓存(styles) ----------

def _registry_with_chain():
    from lxml import etree
    from document2chunk.extractors.docx.styles import StyleRegistry
    styles_xml = f"""<w:styles {DOC_NS}>
      <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="宋体"/></w:rPr></w:rPrDefault></w:docDefaults>
      <w:style w:type="paragraph" w:styleId="Base"><w:name w:val="Base"/></w:style>
      <w:style w:type="paragraph" w:styleId="T1"><w:name w:val="T1"/><w:basedOn w:val="Base"/>
        <w:rPr><w:b/></w:rPr></w:style>
    </w:styles>"""
    reg = StyleRegistry()
    reg.load(etree.fromstring(styles_xml.encode()))
    return reg


def test_red_merged_rpr_caches_chain(monkeypatch):
    from document2chunk.extractors.docx.styles import StyleRegistry
    reg = _registry_with_chain()
    orig = StyleRegistry._chain
    calls: list = []

    def counting_chain(self, style_id):
        calls.append(style_id)
        return orig(self, style_id)

    monkeypatch.setattr(StyleRegistry, "_chain", counting_chain)

    r1 = reg.merged_rpr("T1")
    r2 = reg.merged_rpr("T1")
    r3 = reg.merged_rpr("T1")
    assert len(calls) == 1, f"同一 style 的链只应建一次,实际 {len(calls)} 次"
    assert r1 == r2 == r3
    assert r1.get("bold") is True


# ---------- 3. 页眉前缀惰性(extractor) ----------

def test_red_header_prefix_lazy_stops_after_enough(monkeypatch):
    """header1 已凑满 200 字符 → header2 不再解析;header_text 与全量版一致。"""
    from document2chunk.extractors.docx.extractor import DocxExtractor
    from document2chunk.extractors.docx.package_reader import PackageReader
    long_text = "页" * 250
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p></w:body></w:document>'
    data = make_docx(
        doc,
        header_parts={
            "word/header1.xml": f'<w:hdr {DOC_NS}><w:p><w:r><w:t>{long_text}</w:t></w:r></w:p></w:hdr>',
            "word/header2.xml": f'<w:hdr {DOC_NS}><w:p><w:r><w:t>不该被解析的页眉</w:t></w:r></w:p></w:hdr>',
        },
    )
    counts = _count_read_xml(monkeypatch)

    doc_out = DocxExtractor().extract(data)
    header_text = doc_out.metadata.custom["docx"]["header_text"]
    assert header_text == ("页" * 250)[:200]
    assert counts.get("word/header2.xml", 0) == 0, (
        f"header1 已凑满 200 字符,header2 不应解析,实际 {counts.get('word/header2.xml')} 次")


# ---------- 4. filter_noise 页索引重构守卫 ----------

def test_guard_filter_noise_page_numbers_removed_same_way():
    """页码三证据(窄于上方正文/跨页序列/极端兜底)在重构前后行为一致。"""
    from document2chunk.postprocess import filter_noise
    bodies = ["耕地保护的具体措施包括严格的审批流程和监督机制。",
              "占补平衡需要省级统筹安排落实指标核销工作。",
              "永久基本农田的划定应当符合土地利用总体规划。"]
    content = []
    for pg in range(1, 4):
        content.append(P(str(pg), page=pg, bbox=(280, 810, 310, 820)))
        content.append(P(bodies[pg - 1], page=pg, bbox=(0, 100, 500, 820)))
    out = filter_noise(content, page_geometry=GEO)
    texts = [b.text for b in out if isinstance(b.text, str) and hasattr(b, "text")]
    assert not any(t in ("1", "2", "3") for t in texts)
    assert all(b in texts for b in bodies)


def test_guard_filter_noise_wide_bottom_block_kept():
    """底部宽块(页码宽度判据不命中)→ 保留(防页索引重构改变宽度比较语义)。"""
    from document2chunk.postprocess import filter_noise
    body = "正文段落内容填充文字略长一些以撑起页面高度。"
    content = [
        P(body, page=0, bbox=(0, 100, 500, 820)),
        P("1", page=0, bbox=(0, 810, 500, 820)),  # 底部但与正文同宽 → 非页码
    ]
    out = filter_noise(content, page_geometry=GEO)
    texts = [b.text for b in out]
    assert "1" in texts and body in texts


# ---------- 5. serve 层:detect 单次 + OCR 预切分移除 ----------

def _write_simple_pdf(tmp_path):
    from _fixtures import make_simple_pdf
    p = tmp_path / "simple.pdf"
    p.write_bytes(make_simple_pdf())
    return p


def test_red_serve_detect_runs_once(monkeypatch, tmp_path):
    """路由层已 detect 过,提取器内不应再全文档 detect(同一确定性判定器)。"""
    import document2chunk.api as api_mod
    import document2chunk.extractors.pdf as pdf_mod
    import document2chunk.pipeline.pdf_detect as detect_mod
    from document2chunk import serve

    real = detect_mod.detect_pdf_type
    real_g = detect_mod.detect_pdf_type_guarded
    calls: list = []

    def counting(source, pages=None):
        calls.append(1)
        return real(source, pages=pages)

    def counting_g(source, timeout_s):
        calls.append(1)
        return real_g(source, timeout_s)

    # 路由层默认走 guarded(毒 PDF 看门狗);提取器侧理论上不再直调
    monkeypatch.setattr(detect_mod, "detect_pdf_type_guarded", counting_g)
    monkeypatch.setattr(detect_mod, "detect_pdf_type", counting)
    monkeypatch.setattr(pdf_mod, "detect_pdf_type", counting)
    # 复位可能被其他用例注入的判定器（test_api 会 set_pdf_kind_detector 且不还原），
    # 保证本用例走真实 detect 路径；monkeypatch 退出时还原
    monkeypatch.setattr(api_mod, "_PDF_KIND_DETECTOR", None)

    p = _write_simple_pdf(tmp_path)
    out = tmp_path / "out"
    img = tmp_path / "img"
    out.mkdir()
    img.mkdir()
    serve.parse_to_files(str(p), str(out), str(img))
    assert len(calls) == 1, f"parse_to_files 全程 detect 应只跑 1 次,实际 {len(calls)} 次"


def test_red_serve_ocr_path_no_pre_iteration(monkeypatch, tmp_path):
    """serve 层 OCR 路不应再预跑 iter_pages/page_count 取几何(geo 无人消费)。"""
    import document2chunk.extractors.ocr as ocr_pkg
    import document2chunk.extractors.ocr._chunker as chunker_mod
    from document2chunk import serve
    from document2chunk.ir.enums import SourceType

    iter_calls: list = []
    pc_calls: list = []
    real_iter = chunker_mod.iter_pages
    real_pc = chunker_mod.page_count

    def counting_iter(source, *a, **kw):
        iter_calls.append(1)
        yield from real_iter(source, *a, **kw)

    def counting_pc(source):
        pc_calls.append(1)
        return real_pc(source)

    class FakeExtractor:
        def extract(self, source, *, image_out_dir=None):
            return None

    monkeypatch.setattr(chunker_mod, "iter_pages", counting_iter)
    monkeypatch.setattr(chunker_mod, "page_count", counting_pc)
    monkeypatch.setattr(ocr_pkg, "OcrExtractor", FakeExtractor)

    from _fixtures import make_scanned_pdf_bytes
    serve._extract_with_images(make_scanned_pdf_bytes(), SourceType.OCR, str(tmp_path))
    assert iter_calls == [], f"serve 不应再预跑 iter_pages,实际 {len(iter_calls)} 次"
    assert pc_calls == [], f"serve 不应再调 page_count,实际 {len(pc_calls)} 次"
