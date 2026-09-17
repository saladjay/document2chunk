"""_chai_parse 可观测性：ARRIVE 行 + timer 属性透传 + 错误收口。"""
from __future__ import annotations

import io
import json
import logging
import zipfile

import pytest
from starlette.testclient import TestClient

from document2chunk import api, serve


@pytest.fixture
def client():
    return TestClient(api.create_app())


@pytest.fixture
def timing_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(d))
    return d


def _fake_parse_to_zip(monkeypatch, captured):
    def fake(data, filename=None, **kw):
        captured["timer"] = kw.get("timer")
        captured["filename"] = filename
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("result.md", "# hi\n")
        return buf.getvalue()

    monkeypatch.setattr(serve, "parse_to_zip", fake)


def test_zip_arrive_and_timer_passthrough(client, timing_dir, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    captured = {}
    _fake_parse_to_zip(monkeypatch, captured)
    resp = client.post(
        "/parse-pdf", files={"file": ("报告.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert resp.status_code == 200
    t = captured["timer"]
    assert t.file == "报告.pdf"
    assert t.size_bytes == len(b"%PDF-fake")
    assert t.mode == "zip"
    assert t.demote is False
    assert any("ARRIVE" in r.getMessage() and "报告.pdf" in r.getMessage() for r in caplog.records)


def test_demote_reaches_timer(client, timing_dir, monkeypatch):
    captured = {}
    _fake_parse_to_zip(monkeypatch, captured)
    resp = client.post(
        "/parse-pdf",
        files={"file": ("x.pdf", b"%PDF-fake", "application/pdf")},
        data={"demote": "true"},
    )
    assert resp.status_code == 200
    assert captured["timer"].demote is True


def test_serve_error_finished_as_error(client, timing_dir, monkeypatch):
    def boom(data, filename=None, **kw):
        raise ValueError("x")

    monkeypatch.setattr(serve, "parse_to_zip", boom)
    resp = client.post(
        "/parse-pdf", files={"file": ("x.pdf", b"%PDF-fake", "application/pdf")}
    )
    assert resp.status_code == 500
    files = list(timing_dir.glob("timing-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    assert rec["status"] == "error"
    assert rec["error_type"] == "ValueError"


def test_path_mode_missing_file_arrives_then_errors(client, timing_dir, caplog):
    caplog.set_level(logging.INFO)
    resp = client.post(
        "/parse-pdf",
        files={
            "file_path": (None, "/nonexistent/nope.pdf"),
            "output_dir": (None, "/tmp/out"),
            "image_dir": (None, "/tmp/out/images"),
        },
    )
    assert resp.status_code == 400
    assert any("ARRIVE" in r.getMessage() and "nope.pdf" in r.getMessage() for r in caplog.records)
    files = list(timing_dir.glob("timing-*.jsonl"))
    assert len(files) == 1
    rec = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    assert rec["status"] == "error"
    assert rec["error_type"] == "FileMissing"
