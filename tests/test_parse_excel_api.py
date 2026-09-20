"""POST /parse-excel 端点。"""
from fastapi.testclient import TestClient

from document2chunk import api as api_mod

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _client() -> TestClient:
    return TestClient(api_mod.create_app())


def test_red_openapi_has_parse_excel():
    client = _client()
    assert "/parse-excel" in client.get("/openapi.json").json()["paths"]


def test_red_parse_excel_ok():
    from tests._excel_fixtures import build_xlsx

    client = _client()
    r = client.post(
        "/parse-excel",
        files={"file": ("a.xlsx", build_xlsx({"s": [["名称", "数量"], ["甲", 1]]}), _XLSX_MIME)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["file"] == "a.xlsx"
    assert body["rows"][0]["data"] == {"名称": "甲", "数量": 1}
    assert "warnings" in body and "blocks" in body and "sheets" in body


def test_red_parse_excel_garbage_content_400():
    client = _client()
    r = client.post("/parse-excel", files={"file": ("x.xlsx", b"plain text not excel", "application/octet-stream")})
    assert r.status_code == 400        # UnsupportedFormatError 既有映射
    assert "detail" in r.json()


def test_red_parse_excel_broken_zip_422():
    client = _client()
    r = client.post("/parse-excel", files={"file": ("x.xlsx", b"PK\x03\x04broken", "application/octet-stream")})
    assert r.status_code in (400, 422)  # UNKNOWN→400 或 ExcelParseError→422 均可接受


def test_red_parse_excel_requires_file_or_path():
    client = _client()
    r = client.post("/parse-excel", data={})
    assert r.status_code == 400
