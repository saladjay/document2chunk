"""timing 模块测试：StageTimer 阶段/页/finish 汇总 + JsonlWriter 降级 + ContextVar。"""
from __future__ import annotations

import json
import logging

import pytest

from document2chunk import timing
from document2chunk.timing import JsonlWriter, StageTimer


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(d))
    return d


def _read_jsonl(log_dir):
    files = list(log_dir.glob("timing-*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def test_finish_ok_writes_jsonl_and_stdout(log_dir, caplog):
    caplog.set_level(logging.INFO, logger="document2chunk.timing")
    t = StageTimer(file="a.pdf", size_bytes=10, mode="zip", demote=False, request_id="abc12345")
    with t.stage("detect"):
        pass
    t.ocr_page(1, 0.5, 2)
    t.ocr_page(2, 1.5, 2)
    t.source_type = "ocr"
    t.finish("ok")

    rec = _read_jsonl(log_dir)[-1]
    assert rec["request_id"] == "abc12345"
    assert rec["file"] == "a.pdf"
    assert rec["size_bytes"] == 10
    assert rec["mode"] == "zip"
    assert rec["demote"] is False
    assert rec["status"] == "ok"
    assert rec["source_type"] == "ocr"
    assert "detect" in rec["stages"]
    assert all(v >= 0 for v in rec["stages"].values())
    assert rec["ocr_pages_s"] == [0.5, 1.5]
    assert isinstance(rec["total_s"], float)
    assert "ts" in rec
    # stdout DONE 摘要行（含 request_id）
    assert any("DONE" in r.getMessage() and "abc12345" in r.getMessage() for r in caplog.records)


def test_finish_idempotent(log_dir):
    t = StageTimer(file="a.pdf", mode="zip", request_id="abc12345")
    t.finish("ok")
    t.finish("error", error_type="X")
    lines = _read_jsonl(log_dir)
    assert len(lines) == 1
    assert lines[0]["status"] == "ok"


def test_finish_error_records_error_type(log_dir):
    t = StageTimer(file="a.pdf", mode="zip", request_id="abc12345")
    with t.stage("detect"):
        pass
    t.finish("error", error_type="InvalidPdfError")
    rec = _read_jsonl(log_dir)[-1]
    assert rec["status"] == "error"
    assert rec["error_type"] == "InvalidPdfError"
    assert "detect" in rec["stages"]


def test_jsonl_write_failure_degrades(log_dir, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="document2chunk.timing")
    log_dir.mkdir(parents=True, exist_ok=True)  # fixture 只设 env 不建目录，write_text 不建父目录
    bad = log_dir / "notadir"  # 是文件不是目录 → mkdir 必败
    bad.write_text("x")
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(bad))
    w = JsonlWriter()
    w.write({"x": 1})  # 不应抛异常
    w.write({"x": 2})  # 降级后静默，不再刷 warning
    warns = [r for r in caplog.records if "降级" in r.getMessage()]
    assert len(warns) == 1


def test_log_arrive(caplog):
    caplog.set_level(logging.INFO, logger="document2chunk.timing")
    timing.log_arrive(request_id="abc12345", file="x.pdf", size_bytes=5, mode="zip", from_ip="1.2.3.4")
    assert any("ARRIVE" in r.getMessage() and "x.pdf" in r.getMessage() for r in caplog.records)


def test_current_timer_context():
    assert timing.get_current_timer() is None
    t = StageTimer()
    token = timing.set_current_timer(t)
    try:
        assert timing.get_current_timer() is t
    finally:
        timing.reset_current_timer(token)
    assert timing.get_current_timer() is None
