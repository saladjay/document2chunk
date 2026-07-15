"""ocr-extractor（D11 远程服务版）测试。

- OcrServiceClient：用 httpx.MockTransport 测路由/token/解析/错误。
- OcrExtractor：用 stub client 测 markdown→IR 端到端（无需真实服务）。

运行：PYTHONPATH=src python tests/test_ocr_extractor.py
"""

from __future__ import annotations

from document2chunk.exceptions import OcrServiceError
from document2chunk.extractors._ocr_service import (
    OcrConfig,
    OcrServiceClient,
)
from document2chunk.extractors.ocr import OcrExtractor
from document2chunk.ir import HeadingNode, SourceType, TableNode

try:
    import httpx
    from httpx import MockTransport

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


# ============================================================
# OcrServiceClient（MockTransport）
# ============================================================


def _client(handler, *, token="test-token"):
    cfg = OcrConfig(token=token, endpoint="http://test", poll_interval=0)
    http = httpx.Client(transport=MockTransport(handler))
    return OcrServiceClient(cfg, http_client=http)


def test_service_auth_and_parse():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        if req.url.path == "/api/model-runtime":
            return httpx.Response(200, json={"activeModelId": "paddleocr-vl-1.6"})
        if req.url.path == "/api/paddleocr-vl-1.6":
            return httpx.Response(
                200,
                json={"markdown": "# 标题\n\n正文。", "layoutParsingResults": [{}, {}]},
            )
        return httpx.Response(404)

    c = _client(handler)
    assert c.runtime()["activeModelId"] == "paddleocr-vl-1.6"
    assert seen["auth"] == "Bearer test-token"
    res = c.parse(b"%PDF-1.5 dummy", "a.pdf", model="paddleocr-vl-1.6")
    assert res["markdown"] == "# 标题\n\n正文。"
    assert seen["path"] == "/api/paddleocr-vl-1.6"
    print("OK test_service_auth_and_parse")


def test_ensure_model_already_ready():
    calls = {"switch": 0}

    def handler(req):
        if req.url.path == "/api/model-runtime/switch":
            calls["switch"] += 1
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"activeModelId": "pp-ocrv6"})

    c = _client(handler)
    c.ensure_model("pp-ocrv6")
    assert calls["switch"] == 0
    print("OK test_ensure_model_already_ready")


def test_parse_http_error_raises():
    c = _client(lambda req: httpx.Response(500, text="boom"))
    try:
        c.parse(b"x", "a.pdf", model="pp-ocrv6")
        assert False, "应抛 OcrServiceError"
    except OcrServiceError:
        pass
    print("OK test_parse_http_error_raises")


def test_unknown_model_rejected():
    c = _client(lambda req: httpx.Response(200, json={}))
    try:
        c.parse(b"x", "a.pdf", model="not-a-model")
        assert False
    except OcrServiceError:
        pass
    print("OK test_unknown_model_rejected")


def test_missing_token_raises():
    cfg = OcrConfig(token="")
    c = OcrServiceClient(
        cfg,
        http_client=httpx.Client(transport=MockTransport(lambda r: httpx.Response(200, json={}))),
    )
    try:
        c.runtime()
        assert False, "无 token 应抛 OcrServiceError"
    except OcrServiceError:
        pass
    print("OK test_missing_token_raises")


# ============================================================
# OcrExtractor（stub client）
# ============================================================


class _StubClient:
    """记录调用、返回固定 markdown 的假 OcrServiceClient。"""

    def __init__(self, markdown, layout_pages=None):
        self._md = markdown
        self._layout_pages = layout_pages
        self.last_model = None
        self.last_filename = None

    def ensure_model(self, model_id):
        self.last_model = model_id

    def parse(self, data, filename, *, model):
        self.last_model = model
        self.last_filename = filename
        res = {"markdown": self._md}
        if self._layout_pages is not None:
            res["layoutParsingResults"] = [{} for _ in range(self._layout_pages)]
        return res


def test_extract_markdown_to_ir():
    stub = _StubClient(
        "# 报告标题\n\n## 一、概况\n\n正文段落。\n\n| A | B |\n| - | - |\n| 1 | 2 |"
    )
    ext = OcrExtractor(client=stub)
    result = ext.extract(b"%PDF-1.5 dummy")

    assert result.metadata.source_type == SourceType.OCR
    assert any(isinstance(b, HeadingNode) and b.level == 1 for b in result.content)
    assert any(isinstance(b, HeadingNode) and b.level == 2 for b in result.content)
    assert any(isinstance(b, TableNode) for b in result.content)
    assert stub.last_model == "paddleocr-vl-1.6"  # 默认 VL
    assert all(b.provenance is None for b in result.content)  # 流式，同 docx
    print("OK test_extract_markdown_to_ir")


def test_explicit_model_override():
    stub = _StubClient("# t")
    ext = OcrExtractor(client=stub)
    ext.extract(b"x.png", options={"ocr_model": "pp-ocrv6"})
    assert stub.last_model == "pp-ocrv6"
    print("OK test_explicit_model_override")


def test_long_pdf_selects_unlimited():
    import pymupdf

    doc = pymupdf.open()
    for _ in range(25):
        doc.new_page()
    data = doc.tobytes()
    doc.close()

    stub = _StubClient("# 长", layout_pages=25)
    ext = OcrExtractor(client=stub, long_page_threshold=20)
    r = ext.extract(data, options={"source_file": "long.pdf"})
    assert stub.last_model == "unlimited-ocr", stub.last_model
    assert r.metadata.page_count == 25
    print("OK test_long_pdf_selects_unlimited")


def test_page_count_from_layout():
    stub = _StubClient("# x", layout_pages=7)
    ext = OcrExtractor(client=stub)
    r = ext.extract(b"%PDF-1.5 dummy")
    assert r.metadata.page_count == 7
    print("OK test_page_count_from_layout")


def main():
    if not _HAS_HTTPX:
        print("SKIP service tests (httpx 未安装)")
    else:
        test_service_auth_and_parse()
        test_ensure_model_already_ready()
        test_parse_http_error_raises()
        test_unknown_model_rejected()
        test_missing_token_raises()
    test_extract_markdown_to_ir()
    test_explicit_model_override()
    test_long_pdf_selects_unlimited()
    test_page_count_from_layout()
    print("\nALL OCR EXTRACTOR TESTS PASSED")


if __name__ == "__main__":
    main()
