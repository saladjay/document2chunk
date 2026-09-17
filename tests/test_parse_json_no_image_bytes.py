"""O-1:/parse-json 不再携带 ImageNode.data(图片原始字节)的 TDD 测试。

背景:docx 合成图 bytes 此前以 base64 进 /parse-json 响应,大文档响应膨胀数百 MB。
已确认除 Chai 外无消费方(Chai 走 /parse-pdf zip,不含此字段)→ 剔除安全。
契约:除 bytes 字段外,响应语义不变(image_id/format/markdown 等全保留)。
"""
from __future__ import annotations

import base64

from document2chunk.api import register_extractor, set_markdown_renderer
from document2chunk.ir import (
    DocumentMetadata,
    ExtractionResult,
    ImageNode,
    ParagraphNode,
    SourceType,
)
from test_api import _setup

PNG_BYTES = b"PNGDATA-BYTES-0123456789"


class ImageDocExtractor:
    """产出带 data 字节图的 ExtractionResult(模拟 docx 合成图路径)。"""

    def extract(self, source, *, options=None):
        return ExtractionResult(
            content=[
                ImageNode(id="block_000001", image_id="rId6", format="png", data=PNG_BYTES),
                ParagraphNode(id="block_000002", text="正文内容。"),
            ],
            metadata=DocumentMetadata(source_type=SourceType.DOCX),
        )


def _client():
    _setup()
    register_extractor(SourceType.DOCX, ImageDocExtractor())
    set_markdown_renderer(lambda doc: "# mock markdown")
    from starlette.testclient import TestClient

    return TestClient(__import__("document2chunk.api", fromlist=["create_app"]).create_app())


def test_red_parse_json_omits_image_bytes():
    client = _client()
    r = client.post(
        "/parse-json?source_type=docx&filename=a.docx",
        content=b"PK\x03\x04",
        headers={"content-type": "application/octet-stream"},
    )
    assert r.status_code == 200, r.text
    b64 = base64.b64encode(PNG_BYTES)
    assert b64 not in r.content and PNG_BYTES not in r.content, "图片原始字节不得进 /parse-json 响应"

    body = r.json()
    image = next(n for n in body["document"]["content"] if n.get("image_id"))
    assert "data" not in image, "ImageNode.data 字段应被剔除"
    assert image["image_id"] == "rId6" and image["format"] == "png", "图片元数据必须保留"
    assert body["markdown"] == "# mock markdown"
    assert any(n.get("text") == "正文内容。" for n in body["document"]["content"]), "其余节点不受影响"


def test_red_strip_bytes_fields_unit():
    import document2chunk.api as api_mod

    tree = {
        "a": {"data": b"x", "keep": 1},
        "b": [{"data": b"y", "image_id": "rId1"}],
        "s": "text",
    }
    api_mod._strip_bytes_fields(tree)
    assert "data" not in tree["a"] and tree["a"]["keep"] == 1
    assert "data" not in tree["b"][0] and tree["b"][0]["image_id"] == "rId1"
    assert tree["s"] == "text"
