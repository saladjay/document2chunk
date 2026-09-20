# tests/test_serve_legacy.py
"""serve 层老格式接线：后缀快路径、指纹兜底、xls 400、错误带文件名。

仅 mock serve._legacy_convert（不依赖 soffice）；转换产物用最小 docx（test_docx.make_docx）。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from document2chunk import serve
from document2chunk.exceptions import UnsupportedFormatError

from test_docx import make_docx
from test_format_detect import RANDOM_BYTES, RTF_BYTES, _fake_ole2, _minimal_zip

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOC_XML = (
    f'<w:document xmlns:w="{W}"><w:body>'
    "<w:p><w:r><w:t>转换后正文</w:t></w:r></w:p>"
    "</w:body></w:document>"
)
CONVERTED_DOCX = make_docx(DOC_XML)


def _real_pdf(text: str = "演示转换产物") -> bytes:
    """真最小 PDF（pymupdf 生成）——假 PDF 会触发 _pdf_kind 真检测，必须用真的。

    正文重复 8 遍并用内置中文字体 china-s：pdf_detect 需单页 ≥30 个非空白字符
    才判 editable（否则 empty→mixed→误路由 OCR），默认 helv 字体无法编码中文。
    """
    import pymupdf

    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 72), (text + "。") * 8, fontname="china-s")
    data = d.tobytes()
    d.close()
    return data


def _read_md(zip_bytes: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return z.read("result.md").decode("utf-8")


def _patch_convert(monkeypatch, product: bytes = CONVERTED_DOCX):
    """mock legacy_convert.convert，记录 (in_ext, target)。"""
    calls = []
    monkeypatch.setattr(
        serve, "_legacy_convert",  # serve 内的间接引用点（见 Step 3）
        lambda data, in_ext, target, name=None: (
            calls.append((in_ext, target)) or product
        ),
    )
    return calls


def test_mislabeled_docx_zip_mode(monkeypatch):
    """错标件：filename=.docx 内容 OLE2 → docx 路解析失败 → 指纹兜底 → 转换 → 200。"""
    calls = _patch_convert(monkeypatch)
    md = _read_md(serve.parse_to_zip(_fake_ole2("WordDocument"), "报告.docx"))
    assert "转换后正文" in md
    assert calls and calls[0] == (".doc", ".docx")


def test_doc_suffix_direct_no_exception_first(monkeypatch):
    """真 .doc 后缀：不先错一次，直接转换分支。"""
    calls = _patch_convert(monkeypatch)
    md = _read_md(serve.parse_to_zip(_fake_ole2("WordDocument"), "报告.doc"))
    assert "转换后正文" in md
    assert calls[0] == (".doc", ".docx")


def test_rtf_suffix(monkeypatch):
    calls = _patch_convert(monkeypatch)
    serve.parse_to_zip(RTF_BYTES, "a.rtf")
    assert calls[0] == (".rtf", ".docx")


def test_ppt_suffix_targets_pdf(monkeypatch):
    """ppt → 统一转 PDF：转换目标 .pdf，产物真 PDF 进 PdfExtractor 出正文。"""
    calls = _patch_convert(monkeypatch, product=_real_pdf())
    md = _read_md(serve.parse_to_zip(_fake_ole2("PowerPoint Document"), "讲.ppt"))
    assert calls[0] == (".ppt", ".pdf")
    assert "演示转换产物" in md


def test_docx_renamed_to_doc_no_conversion(monkeypatch):
    """反向错标：真 docx 内容但 .doc 后缀 → 识别归位不转换（PK 嗅探直接走 docx 解析）。"""
    calls = _patch_convert(monkeypatch)
    md = _read_md(serve.parse_to_zip(CONVERTED_DOCX, "a.doc"))
    assert calls == []  # 零转换
    assert "转换后正文" in md


def test_xlsx_suffix_400_with_name():
    with pytest.raises(UnsupportedFormatError) as ei:
        serve.parse_to_zip(_minimal_zip(["xl/workbook.xml"]), "表.xlsx")
    assert "表.xlsx" in str(ei.value)
    assert "/parse-excel" in str(ei.value)


def test_xls_by_fingerprint_400():
    """无后缀裸 OLE2-xls bytes → 规则层命中 XLS → 400 指向 /parse-excel（规则层命中不走 magika）。"""
    with pytest.raises(UnsupportedFormatError) as ei:
        serve.parse_to_zip(_fake_ole2("Workbook"))
    assert "/parse-excel" in str(ei.value)


def test_unknown_bytes_error_has_filename_and_diagnosis():
    with pytest.raises(UnsupportedFormatError) as ei:
        serve.parse_to_zip(RANDOM_BYTES, "怪文件.xyz")
    assert "怪文件.xyz" in str(ei.value)
    assert "规则层" in str(ei.value)  # diagnose 串已拼入


def test_doc_target_env_pdf(monkeypatch):
    """DOCUMENT2CHUNK_DOC_TARGET=pdf → doc 转 pdf。"""
    calls = _patch_convert(monkeypatch, product=_real_pdf())
    monkeypatch.setenv("DOCUMENT2CHUNK_DOC_TARGET", "pdf")
    serve.parse_to_zip(_fake_ole2("WordDocument"), "报告.doc")
    assert calls[0] == (".doc", ".pdf")


def test_path_mode_mislabeled(tmp_path, monkeypatch):
    """路径模式同接线：错标 .docx → 兜底转换 → result.md。"""
    calls = _patch_convert(monkeypatch)
    src = tmp_path / "错标.docx"
    src.write_bytes(_fake_ole2("WordDocument"))
    out, imgs = tmp_path / "out", tmp_path / "images"
    serve.parse_to_files(str(src), str(out), str(imgs))
    assert "转换后正文" in (out / "result.md").read_text(encoding="utf-8")
    assert calls[0] == (".doc", ".docx")
