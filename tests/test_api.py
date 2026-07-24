"""api 冒烟测试（pytest-optional：可 ``python tests/test_api.py`` 直跑）。

extractor / structure / export 由其它 session 提供；本测试用 mock extractor
+ 注入假 structure 模块跑通路由骨架（联调前）。
"""

from __future__ import annotations

import sys
import types

from document2chunk import api
from document2chunk.exceptions import (
    MissingDependencyError,
    UnsupportedFormatError,
)
from document2chunk.ir import (
    DocumentMetadata,
    ExtractionResult,
    HeadingNode,
    ParagraphNode,
    SectionNode,
    SourceType,
)


# ---------------------------------------------------------------------------
# 假 structure 模块（my worktree 无 structure/ —— 注入以测真实 _assemble 路径）
# ---------------------------------------------------------------------------


def _install_fake_structure() -> types.ModuleType:
    if "document2chunk.structure" in sys.modules:
        return sys.modules["document2chunk.structure"]
    mod = types.ModuleType("document2chunk.structure")

    def assemble(result: ExtractionResult, *, keep_toc: bool = False):
        return LogicalDocument(
            metadata=result.metadata,
            content=list(result.content),
            section_tree=SectionNode(id="sec_root", title="ROOT", level=0),
        )

    mod.assemble = assemble
    sys.modules["document2chunk.structure"] = mod
    setattr(__import__("document2chunk"), "structure", mod)
    return mod


# 迟到导入（_install_fake_structure 之后）
from document2chunk.ir import LogicalDocument  # noqa: E402


class MockExtractor:
    """记录调用、产出固定 ExtractionResult。"""

    def __init__(self, source_type: SourceType):
        self.source_type = source_type
        self.last_options = None
        self.calls = 0

    def extract(self, source, *, options=None):
        self.calls += 1
        self.last_options = options
        return ExtractionResult(
            content=[
                HeadingNode(id="block_000001", level=1, text="第一章"),
                ParagraphNode(id="block_000002", text="正文内容。"),
            ],
            metadata=DocumentMetadata(source_type=self.source_type),
        )


def _setup():
    _install_fake_structure()
    api.set_pdf_kind_detector(lambda s: SourceType.PDF)
    api._INSTANCES.clear()


# ---------------------------------------------------------------------------
# 源类型路由
# ---------------------------------------------------------------------------


def test_detect_by_name():
    assert api._detect_by_name("a.docx") == SourceType.DOCX
    assert api._detect_by_name("a.pdf") == SourceType.PDF
    assert api._detect_by_name("a.PNG") == SourceType.OCR
    assert api._detect_by_name("a.xyz") is None
    assert api._detect_by_name(b"bytes") is None


def test_sniff_magic():
    assert api._sniff_source_type(b"%PDF-1.5 ...") == SourceType.PDF
    assert api._sniff_source_type(b"\x89PNG\r\n\x1a\n") == SourceType.OCR
    assert api._sniff_source_type(b"\xff\xd8\xff\xe1") == SourceType.OCR  # JPEG
    assert api._sniff_source_type(b"PK\x03\x04word/") == SourceType.DOCX
    assert api._sniff_source_type(b"plain text") is None


def test_route_explicit_overrides():
    _setup()
    # 显式优先：即便扩展名是 docx，显式给 ocr → OCR
    assert api._route_source_type("a.docx", SourceType.OCR) == SourceType.OCR


def test_route_unknown_raises():
    _setup()
    try:
        api._route_source_type(b"plain", None)
        assert False, "应抛 UnsupportedFormatError"
    except UnsupportedFormatError:
        pass


def test_route_pdf_uses_detector():
    _setup()
    api.set_pdf_kind_detector(lambda s: SourceType.OCR)  # 强制 scanned
    assert api._route_source_type("scan.pdf", None) == SourceType.OCR


# ---------------------------------------------------------------------------
# parse() 端到端骨架（mock extractor + 注入 assemble）
# ---------------------------------------------------------------------------


def test_parse_docx_skeleton():
    _setup()
    api.register_extractor(SourceType.DOCX, MockExtractor(SourceType.DOCX))
    doc = api.parse("a.docx")
    assert isinstance(doc, LogicalDocument)
    assert len(doc.content) == 2
    assert doc.metadata.source_file == "a.docx"
    assert doc.metadata.source_type == SourceType.DOCX
    # options 透传且 extract_images 生效
    ext = api._INSTANCES[SourceType.DOCX]
    assert ext.last_options.extract_images is True


def test_parse_extract_images_false_propagates():
    _setup()
    api.register_extractor(SourceType.DOCX, MockExtractor(SourceType.DOCX))
    api.parse("a.docx", extract_images=False)
    assert api._INSTANCES[SourceType.DOCX].last_options.extract_images is False


def test_parse_bytes_sniff():
    _setup()
    api.register_extractor(SourceType.PDF, MockExtractor(SourceType.PDF))
    # bytes + %PDF 魔数 → 路由 PDF（detector 默认 editable）
    doc = api.parse(b"%PDF-1.5 dummy")
    assert doc.metadata.source_type == SourceType.PDF


def test_parse_unsupported_format_raises():
    _setup()
    # 未实现的格式（html 无 loader）→ UnsupportedFormatError
    try:
        api.parse("x.html")
        assert False, "应抛 UnsupportedFormatError"
    except UnsupportedFormatError:
        pass


def test_parse_real_docx_fixture():
    """端到端：真实 DocxExtractor 解析 fixtures/a.docx → LogicalDocument。

    integration 上 docx-extractor 已就绪（旧版 test_parse_missing_extractor_raises
    假设 docx 未实现，已过时）。这里用真实夹具验证路由→真实 extractor→assemble。
    """
    _setup()
    import os

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "a.docx")
    doc = api.parse(fixture)
    assert isinstance(doc, LogicalDocument)
    assert len(doc.content) >= 1, "docx 应解析出内容"
    assert doc.metadata.source_type == SourceType.DOCX
    assert doc.metadata.source_file == "a.docx"


# ---------------------------------------------------------------------------
# FastAPI /health + /parse
# ---------------------------------------------------------------------------


def test_http_health_and_parse():
    _setup()
    api.register_extractor(SourceType.DOCX, MockExtractor(SourceType.DOCX))
    api.set_markdown_renderer(lambda doc: "# mock markdown")

    from starlette.testclient import TestClient

    client = TestClient(api.create_app())

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # 原始请求体（无需 python-multipart）
    r = client.post(
        "/parse-json?source_type=docx&filename=a.docx",
        content=b"PK\x03\x04",
        headers={"content-type": "application/octet-stream"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "document" in body and "markdown" in body
    assert body["markdown"] == "# mock markdown"
    assert len(body["document"]["content"]) == 2
    assert body["document"]["metadata"]["source_file"] == "a.docx"


def test_http_unsupported_400():
    _setup()
    from starlette.testclient import TestClient

    client = TestClient(api.create_app())
    r = client.post(
        "/parse-json?source_type=html",
        content=b"x",
        headers={"content-type": "application/octet-stream"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def main():
    test_detect_by_name()
    test_sniff_magic()
    test_route_explicit_overrides()
    test_route_unknown_raises()
    test_route_pdf_uses_detector()
    test_parse_docx_skeleton()
    test_parse_extract_images_false_propagates()
    test_parse_bytes_sniff()
    test_parse_unsupported_format_raises()
    test_parse_real_docx_fixture()
    test_http_health_and_parse()
    test_http_unsupported_400()
    print("ALL API TESTS PASSED")


if __name__ == "__main__":
    main()
