"""毒 PDF 看门狗(detect 子进程隔离)的 TDD 测试(docs/大文件提效调研.md §5 序 5)。

已知楔死点:detect 内 page.get_text(MuPDF C 层,killer.pdf 实测烧 CPU 5h)。
防线:detect 在子进程执行,超时 SIGKILL → InvalidSourceError,主服务存活。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from _fixtures import make_simple_pdf


def test_red_main_entry_prints_json(tmp_path):
    """`python -m document2chunk.pipeline.pdf_detect <pdf>` → stdout JSON(pdf_type)。"""
    p = tmp_path / "simple.pdf"
    p.write_bytes(make_simple_pdf())
    r = subprocess.run(
        [sys.executable, "-m", "document2chunk.pipeline.pdf_detect", str(p)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[-200:]}"
    data = json.loads(r.stdout)
    assert data["pdf_type"] == "editable"


def test_red_guarded_returns_result(tmp_path):
    from document2chunk.pipeline.pdf_detect import detect_pdf_type_guarded

    res = detect_pdf_type_guarded(make_simple_pdf(), timeout_s=60)
    assert res.pdf_type == "editable"


def test_red_guarded_timeout_raises(tmp_path, monkeypatch):
    """子进程被测试钩子拖住 → 超时抛 InvalidSourceError(而非挂死主进程)。"""
    from document2chunk.exceptions import InvalidSourceError
    from document2chunk.pipeline.pdf_detect import detect_pdf_type_guarded

    monkeypatch.setenv("DOCUMENT2CHUNK_DETECT_TEST_SLEEP", "30")
    t0 = time.perf_counter()
    with pytest.raises(InvalidSourceError, match="疑似病态 PDF"):
        detect_pdf_type_guarded(make_simple_pdf(), timeout_s=3)
    assert time.perf_counter() - t0 < 15, "超时后应尽快返回,不能等满子进程睡眠"


def test_red_guarded_bad_pdf_raises_with_stderr(tmp_path):
    """子进程内 detect 抛错(rc!=0)→ InvalidSourceError 带 stderr 尾巴。"""
    from document2chunk.exceptions import InvalidSourceError
    from document2chunk.pipeline.pdf_detect import detect_pdf_type_guarded

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(InvalidSourceError):
        detect_pdf_type_guarded(bad.read_bytes(), timeout_s=60)


def test_guard_timeout_env_zero_disables(tmp_path, monkeypatch):
    """DOCUMENT2CHUNK_DETECT_TIMEOUT=0 → api 路由退回进程内直调(兼容开关)。"""
    import document2chunk.api as api_mod

    monkeypatch.setenv("DOCUMENT2CHUNK_DETECT_TIMEOUT", "0")
    called: list = []

    def fake_detect(source, pages=None):
        called.append(1)

        class R:
            pdf_type = "editable"

        return R()

    monkeypatch.setattr(api_mod, "_PDF_KIND_DETECTOR", None)
    import document2chunk.pipeline.pdf_detect as detect_mod
    monkeypatch.setattr(detect_mod, "detect_pdf_type", fake_detect)

    kind = api_mod._pdf_kind(make_simple_pdf())
    assert kind.value == "pdf" or str(kind).endswith("PDF")
    assert called == [1], "timeout=0 时应进程内直调 detect"
