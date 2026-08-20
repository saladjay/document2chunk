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
