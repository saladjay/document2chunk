"""S-1:zip 落盘(parse_to_zip_file)+ api 流式上传/FileResponse 的 TDD 测试(issues7 B 类)。"""
from __future__ import annotations

import io
import zipfile

from test_docx_ooxml import DOC_NS, make_docx


def _docx_bytes():
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>落盘版测试正文</w:t></w:r></w:p></w:body></w:document>'
    return make_docx(doc)


def test_red_parse_to_zip_file_equivalent_to_bytes_version(tmp_path):
    """落盘版与内存版产物逐字节一致;返回 Path;内存版行为不变。"""
    from document2chunk import serve

    data = _docx_bytes()
    out = tmp_path / "resp.zip"
    got_path = serve.parse_to_zip_file(data, "a.docx", out_path=out)
    assert got_path == out and out.exists()
    z_file = zipfile.ZipFile(out)

    z_mem = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(data, "a.docx")))
    assert z_file.namelist() == z_mem.namelist()
    assert z_file.read("result.md") == z_mem.read("result.md")


def test_red_parse_to_zip_file_accepts_path(tmp_path):
    """data 传路径(流式上传场景)与传 bytes 结果一致。"""
    from document2chunk import serve

    src = tmp_path / "in.docx"
    src.write_bytes(_docx_bytes())
    out = tmp_path / "resp2.zip"
    serve.parse_to_zip_file(str(src), "a.docx", out_path=out)
    z_file = zipfile.ZipFile(out)
    z_mem = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(src.read_bytes(), "a.docx")))
    assert z_file.namelist() == z_mem.namelist()
    assert z_file.read("result.md") == z_mem.read("result.md")


def test_red_api_zip_mode_streams_via_file(tmp_path):
    """/parse-pdf zip 模式走落盘 FileResponse:200 + 内容正确 + attachment 头。"""
    from starlette.testclient import TestClient

    import document2chunk.api as api_mod
    from document2chunk import serve

    api_mod._setup_fakes_for_tests() if hasattr(api_mod, "_setup_fakes_for_tests") else None

    client = TestClient(api_mod.create_app())
    data = _docx_bytes()
    r = client.post(
        "/parse-pdf",
        files={"file": ("a.docx", data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    assert "attachment" in r.headers.get("content-disposition", "")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "result.md" in z.namelist()
    assert "落盘版测试正文".encode() in z.read("result.md")
