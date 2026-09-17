"""OCR 页级计时：current_timer 回填 ocr_pages_s + ocr_page 行。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from document2chunk import timing
from document2chunk.extractors.ocr import extractor as ex_mod
from document2chunk.extractors.ocr.extractor import OcrExtractor


class _FakeClient:
    def parse(self, media, fname, *, model):
        return {"markdown": "# 页", "images": {}}


def _fake_iter_pages(data, name):
    for i in range(2):
        yield i, b"img", f"p{i}.png", 100, 100


@pytest.fixture
def fake_pages(monkeypatch):
    monkeypatch.setattr(ex_mod, "iter_pages", _fake_iter_pages)
    monkeypatch.setattr(ex_mod, "page_count", lambda data: 2)


def test_ocr_pages_recorded(fake_pages, caplog):
    caplog.set_level(logging.INFO, logger="document2chunk.timing")
    t = timing.StageTimer(file="s.pdf", mode="zip", request_id="ocr12345")
    token = timing.set_current_timer(t)
    try:
        opts = SimpleNamespace(extract_images=True, ocr_model="vl")
        OcrExtractor(client=_FakeClient()).extract(b"data", options=opts)
    finally:
        timing.reset_current_timer(token)

    assert len(t.ocr_pages_s) == 2
    assert all(v >= 0 for v in t.ocr_pages_s)
    assert any("ocr_page" in r.getMessage() and "1/2" in r.getMessage() for r in caplog.records)


def test_no_timer_is_noop(fake_pages):
    opts = SimpleNamespace(extract_images=True, ocr_model="vl")
    res = OcrExtractor(client=_FakeClient()).extract(b"data", options=opts)  # 不应抛异常
    assert res is not None
