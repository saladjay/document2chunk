"""serve（/parse-pdf、CLI 共用路径）测试：docx 路由 + 单次后处理。"""

from __future__ import annotations

import io
import zipfile

from document2chunk import serve
from document2chunk.ir import SourceType

from test_docx import make_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

DOC_XML = (
    f'<w:document xmlns:w="{W}"><w:body>'
    '<w:p><w:r><w:t>测试文档正文段落</w:t></w:r></w:p>'
    "</w:body></w:document>"
)
DOCX_BYTES = make_docx(DOC_XML)


def _read_md(zip_bytes: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert "result.md" in z.namelist(), z.namelist()
    return z.read("result.md").decode("utf-8")


def test_parse_to_zip_docx_avoids_pdf_pipeline(monkeypatch):
    """docx 字节不得进 PDF 管线（现状缺陷：PyMuPDF 硬解 docx 产出伪标题垃圾）。"""
    from document2chunk.extractors.pdf import PdfExtractor

    def _boom(self, source, **kw):  # noqa: ANN001
        raise AssertionError("docx 误入 PDF 管线")

    monkeypatch.setattr(PdfExtractor, "extract", _boom)
    md = _read_md(serve.parse_to_zip(DOCX_BYTES, "t.docx"))
    assert "测试文档正文段落" in md


def test_parse_to_files_docx_routes_by_extension(tmp_path):
    """路径模式按 .docx 扩展名路由 → metadata.source_type == DOCX。"""
    src = tmp_path / "t.docx"
    src.write_bytes(DOCX_BYTES)
    out, imgs = tmp_path / "out", tmp_path / "images"
    doc = serve.parse_to_files(str(src), str(out), str(imgs))
    assert (out / "result.md").read_text(encoding="utf-8").find("测试文档正文段落") >= 0
    assert doc.metadata.source_type == SourceType.DOCX


def test_postprocess_runs_exactly_once(monkeypatch):
    """统一后处理全量恰好 1 次（extractor 内部），serve 层不再叠加第二遍。"""
    import document2chunk.postprocess as pp_mod

    calls: list[int] = []
    orig = pp_mod.postprocess

    def _spy(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    monkeypatch.setattr(pp_mod, "postprocess", _spy)
    serve.parse_to_zip(DOCX_BYTES, "t.docx")
    assert len(calls) == 1, f"postprocess 应恰好 1 次，实际 {len(calls)} 次"


def test_parse_to_zip_docx_image_refs_match_files():
    """含图 docx：result.md 引用 images/<媒体名> 且 zip 内确有该文件（上线验收线）。"""
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    NS = f'xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}"'
    doc_xml = (
        f'<w:document {NS}><w:body>'
        "<w:p><w:r><w:t>正文</w:t></w:r></w:p>"
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="50"/><wp:docPr descr="logo"/>'
        '<a:graphic><a:blip r:embed="rId7"/></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        "</w:body></w:document>"
    )
    data = make_docx(
        doc_xml,
        media={"word/media/image1.png": b"PNGDATA"},
        rels_xml=(
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId7" Type="{R}/image" Target="media/image1.png"/>'
            "</Relationships>"
        ),
    )
    zip_bytes = serve.parse_to_zip(data, "t.docx")
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    md = z.read("result.md").decode("utf-8")
    assert "](images/image1.png)" in md, md
    assert "images/rId7" not in md, md
    assert "images/image1.png" in z.namelist(), z.namelist()
