"""`/parse-doc` 接口:`/parse-pdf` 的别名(同一 handler、同一契约)的 TDD 测试。"""
from __future__ import annotations

import io
import zipfile

from starlette.testclient import TestClient

import document2chunk.api as api_mod


def _client():
    return TestClient(api_mod.create_app())


def test_red_openapi_contains_parse_doc():
    """OpenAPI schema 暴露 /parse-doc 路径。"""
    schema = _client().get("/openapi.json").json()
    assert "/parse-doc" in schema["paths"], f"paths={list(schema['paths'])}"


def test_red_parse_doc_zip_mode_same_as_parse_pdf(tmp_path):
    """同一载荷下 /parse-doc 与 /parse-pdf 响应完全一致(200 + 同 zip 内容)。"""
    payload = "alias smoke\n\nparse-doc 别名接口测试正文。\n"
    resp_by_path = {}
    for path in ("/parse-doc", "/parse-pdf"):
        r = _client().post(path, files={"file": ("a.txt", payload.encode("utf-8"), "text/plain")})
        assert r.status_code == 200, r.text
        resp_by_path[path] = r
    a, b = resp_by_path["/parse-doc"], resp_by_path["/parse-pdf"]
    assert a.content == b.content, "别名必须返回相同产物"
    z = zipfile.ZipFile(io.BytesIO(a.content))
    assert "result.md" in z.namelist()
    assert "parse-doc 别名接口测试正文。".encode() in z.read("result.md")


def test_red_parse_doc_error_contract_same():
    """错误契约一致:非 multipart → 400(与 /parse-pdf 同)。"""
    for path in ("/parse-doc", "/parse-pdf"):
        r = _client().post(path, content=b"x", headers={"content-type": "text/plain"})
        assert r.status_code == 400, f"{path}: {r.status_code}"
