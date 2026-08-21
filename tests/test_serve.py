"""serve（/parse-pdf、CLI 共用路径）测试：docx 路由 + 单次后处理 + .md 直通 + .txt 转录。"""

from __future__ import annotations

import io
import zipfile

import pytest

from document2chunk import serve
from document2chunk.exceptions import UnsupportedFormatError
from document2chunk.ir import SourceType

from test_docx import make_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

DOC_XML = (
    f'<w:document xmlns:w="{W}"><w:body>'
    '<w:p><w:r><w:t>测试文档正文段落</w:t></w:r></w:p>'
    "</w:body></w:document>"
)
DOCX_BYTES = make_docx(DOC_XML)

MD_BYTES = "# 测试\n\n正文".encode("utf-8")


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


# ------------------------------------------------------------------
# .md 直通：/parse-pdf 遇 md 文件跳过解析、输出原件
# ------------------------------------------------------------------


def test_parse_to_zip_md_passthrough():
    """zip 模式 .md 直通：不解析，zip 恰含 result.md 且为原始字节。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(MD_BYTES, "t.md")))
    assert z.namelist() == ["result.md"], z.namelist()
    assert z.read("result.md") == MD_BYTES


def test_parse_to_zip_md_uppercase_ext():
    """大写 .MD 扩展名同样直通（大小写不敏感）。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(MD_BYTES, "T.MD")))
    assert z.namelist() == ["result.md"], z.namelist()
    assert z.read("result.md") == MD_BYTES


def test_parse_to_files_md_passthrough(tmp_path):
    """路径模式 .md 直通：result.md = 原件字节，返回最小文档（content=[]）。"""
    src = tmp_path / "t.md"
    src.write_bytes(MD_BYTES)
    out, imgs = tmp_path / "out", tmp_path / "images"
    doc = serve.parse_to_files(str(src), str(out), str(imgs))
    assert (out / "result.md").read_bytes() == MD_BYTES
    assert doc.content == []
    assert doc.metadata.source_file == "t.md"


# ------------------------------------------------------------------
# .txt 转录：/parse-pdf 遇 txt 文件解码→UTF-8 无 BOM 后输出 result.md
# ------------------------------------------------------------------

TXT_UTF8 = "第一行中文\nsecond line\n".encode("utf-8")


def test_parse_to_zip_txt_utf8_passthrough():
    """zip 模式 UTF-8 txt：转录后 result.md 与原件逐字节一致。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(TXT_UTF8, "t.txt")))
    assert z.namelist() == ["result.md"], z.namelist()
    assert z.read("result.md") == TXT_UTF8


def test_parse_to_zip_txt_gbk_transcoded():
    """zip 模式 GBK txt：回退 GB18030 解码，result.md 为同文本 UTF-8 字节。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip("测试中文内容".encode("gbk"), "t.txt")))
    assert z.read("result.md") == "测试中文内容".encode("utf-8")


def test_parse_to_zip_txt_bom_stripped():
    """UTF-8 BOM 头剥除：输出无 BOM。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(b"\xef\xbb\xbf" + TXT_UTF8, "t.txt")))
    assert z.read("result.md") == TXT_UTF8


def test_parse_to_zip_txt_undecodable_unsupported():
    """UTF-8 / GB18030 均解码失败 → UnsupportedFormatError。"""
    with pytest.raises(UnsupportedFormatError):
        serve.parse_to_zip(b"\xff\xff\xff", "t.txt")


def test_parse_to_zip_txt_none_filename_unsupported():
    """filename=None 的 bytes 无扩展名依据，不判 txt，维持报错。"""
    with pytest.raises(UnsupportedFormatError):
        serve.parse_to_zip(TXT_UTF8, None)


def test_parse_to_files_txt_transcoded(tmp_path):
    """路径模式 .txt：GBK 转码写盘 result.md，返回最小文档（content=[]）。"""
    src = tmp_path / "t.txt"
    src.write_bytes("测试中文内容".encode("gbk"))
    out, imgs = tmp_path / "out", tmp_path / "images"
    doc = serve.parse_to_files(str(src), str(out), str(imgs))
    assert (out / "result.md").read_bytes() == "测试中文内容".encode("utf-8")
    assert doc.content == []
    assert doc.metadata.source_file == "t.txt"


def test_parse_to_zip_md_none_filename_unsupported():
    """filename=None 的 bytes 无扩展名依据，不判 md，维持报错。"""
    with pytest.raises(UnsupportedFormatError):
        serve.parse_to_zip(b"# x", None)
