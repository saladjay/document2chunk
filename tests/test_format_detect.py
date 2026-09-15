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
