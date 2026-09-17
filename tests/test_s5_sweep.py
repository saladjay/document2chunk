"""S-5 四个小项的 TDD 测试(issues7 B 类)。

S-5a legacy 兜底门控:identify 判定"内容可解析"(DOCX/PDF/IMAGE)时直接重抛首轮错误,
    跳过无谓的转换 + 第二次全档解析(结果等价:同一错误);真老格式(DOC/RTF/WPS/PPT)仍走转换。
S-5b geo_ocr 引擎单例:同进程复用 PaddleOCR 引擎,不再每表新建。
S-5c 留痕轮转:_save_zip_artifacts 按天清理过期子目录(env DOCUMENT2CHUNK_SAVE_RETENTION_DAYS,默认 30,0=关)。
S-5d active_model 缓存:模块级 TTL 缓存,一次解析只探测一次(env DOCUMENT2CHUNK_OCR_MODEL_TTL,默认 300,0=关)。
"""
from __future__ import annotations

import os
import time

import pytest

from test_docx_ooxml import DOC_NS, make_docx


# ---------- S-5a legacy 兜底门控 ----------

def _docx_bytes():
    doc = f'<w:document {DOC_NS}><w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>'
    return make_docx(doc)


def test_red_parseable_content_skips_second_run(monkeypatch):
    """内容可解析(PDF 字节)却因 .docx 后缀解析失败 → 直接重抛,不二次全档解析。"""
    from _fixtures import make_simple_pdf
    from document2chunk import serve
    from document2chunk.extractors.docx import DocxExtractor
    import zipfile

    calls = {"extract": 0, "convert": 0}
    real_extract = DocxExtractor.extract

    def counting_extract(self, source, **kw):
        calls["extract"] += 1
        return real_extract(self, source, **kw)

    monkeypatch.setattr(DocxExtractor, "extract", counting_extract)
    monkeypatch.setattr(
        serve, "_legacy_convert",
        lambda *a, **kw: calls.__setitem__("convert", calls["convert"] + 1),
    )
    # 路径模式:后缀 .docx 路由到 DocxExtractor,内容却是 PDF → BadZipFile → 兜底
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "a.docx")
        with open(src, "wb") as f:
            f.write(make_simple_pdf())
        out = os.path.join(td, "out")
        img = os.path.join(td, "img")
        os.makedirs(out)
        os.makedirs(img)
        with pytest.raises(zipfile.BadZipFile):
            serve.parse_to_files(src, out, img)
    assert calls["extract"] == 1, f"可解析内容不应二次解析,实际 extract {calls['extract']} 次"
    assert calls["convert"] == 0, "可解析内容不应尝试转换"


def test_guard_real_legacy_still_converts(monkeypatch):
    """真老格式(RTF 内容 + .docx 后缀)仍走转换并成功解析(等价性守卫)。"""
    from document2chunk import serve

    calls = {"convert": 0}

    def fake_convert(data, in_ext, target, name=None):
        calls["convert"] += 1
        return _docx_bytes()

    monkeypatch.setattr(serve, "_legacy_convert", fake_convert)
    rtf = b"{\\rtf1\\ansi hello}"
    serve.parse_to_zip(rtf, "a.docx")  # 现代后缀 + 老内容 → 走内容指纹兜底
    assert calls["convert"] == 1, "真老格式必须仍走转换"


# ---------- S-5b geo_ocr 引擎单例 ----------

def test_red_default_ocr_singleton(monkeypatch):
    import sys
    import document2chunk.extractors.table._cell_ocr as co

    made = []

    class FakePaddleOCR:
        def __init__(self, **kw):
            made.append(1)

    monkeypatch.setitem(sys.modules, "paddleocr", type("M", (), {"PaddleOCR": FakePaddleOCR}))
    a = co._default_ocr()
    b = co._default_ocr()
    assert a is b, "引擎应复用同一实例"
    assert len(made) == 1, f"应只构造一次,实际 {len(made)} 次"


# ---------- S-5c 留痕轮转 ----------

def test_red_artifacts_rotation_removes_old(monkeypatch, tmp_path):
    from document2chunk import serve

    save_dir = tmp_path / "out"
    save_dir.mkdir()
    old = save_dir / "20200101-000000-000000__old.docx"
    old.mkdir()
    (old / "request.bin").write_bytes(b"x")
    fresh = save_dir / "20990101-000000-000000__new.docx"
    fresh.mkdir()

    monkeypatch.setenv("DOCUMENT2CHUNK_SAVE_RETENTION_DAYS", "30")
    serve._save_zip_artifacts(str(save_dir), "n.docx", b"d", b"z")
    assert not old.exists(), "过期留痕目录应被清理"
    assert fresh.exists(), "未过期目录不动"
    assert any(save_dir.iterdir()), "本次留痕仍在"


def test_guard_artifacts_rotation_disabled(monkeypatch, tmp_path):
    from document2chunk import serve

    save_dir = tmp_path / "out2"
    save_dir.mkdir()
    old = save_dir / "20200101-000000-000000__old.docx"
    old.mkdir()
    monkeypatch.setenv("DOCUMENT2CHUNK_SAVE_RETENTION_DAYS", "0")
    serve._save_zip_artifacts(str(save_dir), "n.docx", b"d", b"z")
    assert old.exists(), "RETENTION=0 应完全禁用清理"


# ---------- S-5d active_model 缓存 ----------

def test_red_active_model_cached(monkeypatch):
    from _fixtures import make_multipage_pdf
    from document2chunk.extractors.ocr import OcrExtractor
    from document2chunk.extractors.ocr._config import OcrConfig
    from test_ocr_extractor import RESP
    import copy

    class CountingClient:
        def __init__(self):
            self.active_calls = 0

        def active_model(self):
            self.active_calls += 1
            return "unlimited"

        def parse(self, media, filename, *, model):
            return copy.deepcopy(RESP)

    monkeypatch.delenv("DOCUMENT2CHUNK_OCR_MODEL_TTL", raising=False)
    import document2chunk.extractors.ocr.extractor as ex_mod
    ex_mod._ACTIVE_MODEL_CACHE.clear()

    cfg = OcrConfig(endpoint="http://fake-cache-test", token="", timeout=30.0, concurrency=1)
    c = CountingClient()
    ext = OcrExtractor(config=cfg, client=c)
    ext.extract(make_multipage_pdf(2))
    ext.extract(make_multipage_pdf(2))
    assert c.active_calls == 1, f"两次解析应只探测一次模型,实际 {c.active_calls} 次"


def test_guard_active_model_ttl_zero_disables(monkeypatch):
    from _fixtures import make_multipage_pdf
    from document2chunk.extractors.ocr import OcrExtractor
    from document2chunk.extractors.ocr._config import OcrConfig
    from test_ocr_extractor import RESP
    import copy

    class CountingClient:
        def __init__(self):
            self.active_calls = 0

        def active_model(self):
            self.active_calls += 1
            return "unlimited"

        def parse(self, media, filename, *, model):
            return copy.deepcopy(RESP)

    monkeypatch.setenv("DOCUMENT2CHUNK_OCR_MODEL_TTL", "0")
    import document2chunk.extractors.ocr.extractor as ex_mod
    ex_mod._ACTIVE_MODEL_CACHE.clear()

    cfg = OcrConfig(endpoint="http://fake-cache-test2", token="", timeout=30.0, concurrency=1)
    c = CountingClient()
    ext = OcrExtractor(config=cfg, client=c)
    ext.extract(make_multipage_pdf(1))
    ext.extract(make_multipage_pdf(1))
    assert c.active_calls == 2, "TTL=0 应每次探测(现行为)"
