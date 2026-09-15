# tests/test_format_detect.py
"""format_detect 规则层：魔数/zip 条目/OLE2 流名/rtf 全矩阵 + 错标件 + 脏输入。"""

from __future__ import annotations

import io
import zipfile

from document2chunk.format_detect import FileKind, identify

PDF_BYTES = b"%PDF-1.7\nfake pdf body"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 32
RTF_BYTES = rb"{\rtf1\ansi\ansicpg936 \u25105\u26159 RTF \u27491\u25991}"
RANDOM_BYTES = bytes(range(256)) * 4  # 无任何已知魔数


def _minimal_zip(entries: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in entries:
            z.writestr(n, b"x")
    return buf.getvalue()


# 手工 OLE2：真实文件头 + 目录扇区放 UTF-16LE 流名（规则层原始扫描即可命中）
def _fake_ole2(stream_name: str) -> bytes:
    head = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 56
    return head + stream_name.encode("utf-16-le") + b"\x00" * 64


def test_pdf_magic():
    assert identify(PDF_BYTES).kind == FileKind.PDF

def test_image_magics():
    assert identify(PNG_BYTES).kind == FileKind.IMAGE
    assert identify(JPEG_BYTES).kind == FileKind.IMAGE

def test_zip_family():
    assert identify(_minimal_zip(["word/document.xml", "[Content_Types].xml"])).kind == FileKind.DOCX
    assert identify(_minimal_zip(["xl/workbook.xml"])).kind == FileKind.XLSX
    assert identify(_minimal_zip(["ppt/presentation.xml"])).kind == FileKind.PPTX
    assert identify(_minimal_zip(["a.txt", "b/c.txt"])).kind == FileKind.ZIP

def test_ole2_family():
    assert identify(_fake_ole2("WordDocument")).kind == FileKind.DOC
    assert identify(_fake_ole2("Workbook")).kind == FileKind.XLS
    assert identify(_fake_ole2("PowerPoint Document")).kind == FileKind.PPT

def test_rtf_rules_layer_not_magika():
    d = identify(RTF_BYTES)
    assert d.kind == FileKind.RTF
    assert d.layer == "rules"  # RTF 定死规则层（magika 误判 txt）

def test_mislabeled_docx_named_but_ignored():
    """错标件：.docx 后缀 OLE2 内容——identify 只看内容，kind == DOC。"""
    d = identify(_fake_ole2("WordDocument"))
    assert d.kind == FileKind.DOC
    assert d.ext == ".doc"

def test_unknown_safe():
    for dirty in (b"", RANDOM_BYTES, b"\x00" * 8, b"PK\x03"):  # 截断/空/半魔数
        assert identify(dirty).kind == FileKind.UNKNOWN
        assert identify(dirty).ext == ""

def test_detection_ext_mapping():
    assert identify(PDF_BYTES).ext == ".pdf"
    assert identify(_minimal_zip(["word/document.xml"])).ext == ".docx"
    assert identify(RTF_BYTES).ext == ".rtf"


# tests/test_format_detect.py 追加
import sys
from unittest.mock import MagicMock

import document2chunk.format_detect as fd


def test_magika_fallback_adopted(monkeypatch):
    """规则层未命中 → magika label 非 unknown/zip → 采纳为 kind。"""
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)  # 重置模型缓存（防测试间泄漏）
    fake = MagicMock()
    fake.Magika.return_value.identify_bytes.return_value.output.label = "wps"
    monkeypatch.setitem(sys.modules, "magika", fake)
    d = fd.identify(RANDOM_BYTES)
    assert d.kind == FileKind.WPS
    assert d.layer == "magika"


def test_magika_unknown_abstains(monkeypatch):
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)
    fake = MagicMock()
    fake.Magika.return_value.identify_bytes.return_value.output.label = "unknown"
    monkeypatch.setitem(sys.modules, "magika", fake)
    assert fd.identify(RANDOM_BYTES).layer == "none"


def test_magika_import_error_graceful(monkeypatch):
    """未安装 magika → 规则层结果原样返回，不抛。"""
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)
    monkeypatch.setitem(sys.modules, "magika", None)  # import 触发 ImportError
    assert fd.identify(PDF_BYTES).kind == FileKind.PDF
    assert fd.identify(RANDOM_BYTES).layer == "none"


def test_diagnose_message_contains_name_and_hex():
    msg = fd.diagnose(RANDOM_BYTES, "报告.docx")
    assert "报告.docx" in msg
    assert "00-01-02" in msg  # 魔数前字节 hex


def test_magika_model_cached(monkeypatch):
    """Magika 实例只构建一次（模型 5ms/次，缓存避免重复加载）。"""
    fake = MagicMock()
    fake.Magika.return_value.identify_bytes.return_value.output.label = "unknown"
    monkeypatch.setitem(sys.modules, "magika", fake)
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)  # 重置缓存
    fd.identify(RANDOM_BYTES)
    fd.identify(RANDOM_BYTES)
    assert fake.Magika.call_count == 1
