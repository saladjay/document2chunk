"""round-2 OCR 页级并发 + partial 部分结果 的 TDD 测试(docs/大文件提效调研.md §5 序 3)。

约定:RED 用例实现前必须失败;GUARD 用例改前改后都须通过。
设计红线:fetch 并发只做 HTTP;映射/发 id/计时在主线程按页序串行 →
输出与串行版逐字节一致(§7 序 3 等价前提)。
"""
from __future__ import annotations

import copy
import time

import pytest

from _fixtures import make_multipage_pdf
from document2chunk.extractors.ocr import OcrExtractor
from document2chunk.extractors.ocr._config import OcrConfig
from document2chunk.extractors.ocr._exceptions import OcrServiceError
from test_ocr_extractor import RESP

CFG = dict(endpoint="http://fake", token="", timeout=30.0)


class InstantClient:
    """每页返回同一份合成响应(与 test_ocr_extractor.FakeClient 同)。"""

    def active_model(self):
        return "unlimited"

    def __init__(self):
        self.calls: list = []

    def parse(self, media, filename, *, model):
        self.calls.append(filename)
        return copy.deepcopy(RESP)


class SlowReversedClient(InstantClient):
    """页号越小越慢 → 并发下完成顺序与页序相反(考验归并保序)。"""

    def __init__(self, base=0.35):
        super().__init__()
        self._base = base

    def parse(self, media, filename, *, model):
        n = self._page_no(filename)
        time.sleep(self._base - 0.08 * (n - 1))
        return super().parse(media, filename, model=model)

    @staticmethod
    def _page_no(filename):
        # iter_pages 的 fname 形如 <src>_p001.pdf
        import re
        m = re.search(r"_p(\d+)", filename or "")
        return int(m.group(1)) if m else 1


class FlakyClient(InstantClient):
    """第 fail_call 次调用抛 OcrServiceError(concurrency=1 时即第 fail_call 页)。"""

    def __init__(self, fail_call: int):
        super().__init__()
        self._fail_call = fail_call

    def parse(self, media, filename, *, model):
        if len(self.calls) + 1 == self._fail_call:
            self.calls.append(filename)
            raise OcrServiceError("boom: page failed")
        return super().parse(media, filename, model=model)


def _extract(client, **cfg_extra):
    cfg = OcrConfig(**{**CFG, **cfg_extra})
    return OcrExtractor(config=cfg, client=client).extract(make_multipage_pdf(4))


# ---------- RED 1: 配置字段 ----------

def test_red_config_concurrency_and_partial_fields(monkeypatch):
    monkeypatch.delenv("DOCUMENT2CHUNK_OCR_CONCURRENCY", raising=False)
    monkeypatch.delenv("DOCUMENT2CHUNK_OCR_PARTIAL", raising=False)
    cfg = OcrConfig.from_env()
    assert cfg.concurrency == 4, "默认并发应为 4"
    assert cfg.partial_ok is False, "默认全有全无"

    monkeypatch.setenv("DOCUMENT2CHUNK_OCR_CONCURRENCY", "6")
    monkeypatch.setenv("DOCUMENT2CHUNK_OCR_PARTIAL", "1")
    cfg2 = OcrConfig.from_env()
    assert cfg2.concurrency == 6 and cfg2.partial_ok is True

    assert OcrConfig(**CFG, concurrency=1).concurrency == 1


# ---------- RED 2: 并发提速 + 完成顺序颠倒仍按页序归并 ----------

def test_red_concurrent_fetch_speedup_and_page_order():
    t0 = time.perf_counter()
    result = _extract(SlowReversedClient(), concurrency=4)
    wall = time.perf_counter() - t0
    # 串行下限 = 0.35+0.27+0.19+0.11 ≈ 0.92s;并发 4 上限 ≈ 0.35s
    assert wall < 0.75, f"4 页应并行完成,实际 {wall:.2f}s"
    pages = [b.provenance.page_index for b in result.content if b.provenance]
    assert pages == sorted(pages), "完成顺序颠倒时块仍必须按页序排列"


# ---------- RED 3: partial_ok=True 跳过失败页并记录 ----------

def test_red_partial_ok_skips_failed_page():
    result = _extract(FlakyClient(fail_call=2), concurrency=1, partial_ok=True)
    got_pages = {b.provenance.page_index for b in result.content if b.provenance}
    assert got_pages == {0, 2, 3}, "失败页(第2页,0基=1)应被跳过"
    assert result.metadata.custom.get("ocr", {}).get("failed_pages") == [2]


# ---------- RED 4: 默认全有全无(契约不变) ----------

def test_red_default_all_or_nothing_preserved():
    with pytest.raises(OcrServiceError):
        _extract(FlakyClient(fail_call=2), concurrency=1, partial_ok=False)


# ---------- GUARD: 计时按页序回填 ----------

def test_guard_ocr_page_timing_in_page_order(monkeypatch):
    recorded: list = []

    class StubTimer:
        def ocr_page(self, page_no, elapsed, total):
            recorded.append(page_no)

    import document2chunk.timing as timing_mod
    monkeypatch.setattr(timing_mod, "get_current_timer", lambda: StubTimer())

    _extract(SlowReversedClient(base=0.12), concurrency=4)
    assert recorded == [1, 2, 3, 4], "ocr_page 计时必须按页序回填"
