"""_chai_parse 可观测性：ARRIVE 行 + timer 属性透传 + 错误收口 + ARRIVE→DONE 顺序 + stdout 可见性。"""
from __future__ import annotations

import io
import json
import logging
import os
import socket
import subprocess
import sys
import time
import zipfile

import pytest
from starlette.testclient import TestClient

from document2chunk import api, serve

from test_serve import DOCX_BYTES


@pytest.fixture
def client():
    return TestClient(api.create_app())


@pytest.fixture
def timing_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(d))
    return d


def _fake_parse_to_zip(monkeypatch, captured):
    from pathlib import Path

    def fake(data, filename=None, **kw):
        captured["timer"] = kw.get("timer")
        captured["filename"] = filename
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("result.md", "# hi\n")
        out = Path(kw["out_path"])
        out.write_bytes(buf.getvalue())
        return out

    monkeypatch.setattr(serve, "parse_to_zip_file", fake)


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

    monkeypatch.setattr(serve, "parse_to_zip_file", boom)
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


def test_arrive_precedes_done(client, timing_dir, caplog):
    """ARRIVE 行必须先于 DONE 行（真 docx 走完整路径，DONE 由 serve 内 timer.finish 打出）。"""
    caplog.set_level(logging.INFO)
    resp = client.post(
        "/parse-pdf",
        files={"file": ("t.docx", DOCX_BYTES, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert resp.status_code == 200
    # 只看 timing logger 的 INFO 行，按捕获序号比较先后
    seq = [
        (i, r.getMessage())
        for i, r in enumerate(caplog.records)
        if r.levelno == logging.INFO and r.name == "document2chunk.timing"
    ]
    arrive = [i for i, msg in seq if "ARRIVE" in msg]
    done = [i for i, msg in seq if "DONE" in msg]
    assert arrive and done, [m for _, m in seq]
    assert arrive[0] < done[0], seq


def test_main_stdout_shows_arrive_and_done(tmp_path):
    """C1 回归：生产入口 main() 的 basicConfig 让 ARRIVE/DONE 透出到进程 stdout。

    子进程级验证（慢测试可接受）：uvicorn 默认 log_config 不给 root logger 装
    handler，不修的话 INFO 行被 Python lastResort 丢弃（只透出 WARNING+）；
    修复后 uvicorn 随后的 dictConfig（disable_existing_loggers=False）不拆
    basicConfig 装上的 root handler。stderr 合并进 stdout（basicConfig 默认走 stderr）。
    """
    import httpx

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))  # 绑 0 拿空闲端口
    port = sock.getsockname()[1]
    sock.close()

    env = dict(os.environ, DOCUMENT2CHUNK_LOG_DIR=str(tmp_path / "logs"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "document2chunk.api", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    out = b""
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15.0
        up = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # 启动失败早退——带输出直接报
                out, _ = proc.communicate(timeout=5)
                raise AssertionError(
                    f"服务进程早退 rc={proc.returncode}: {out.decode('utf-8', 'replace')[:2000]}"
                )
            try:
                if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                    up = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        assert up, "服务 15s 内未就绪"
        resp = httpx.post(
            f"{base}/parse-pdf",
            files={"file": ("t.md", b"# hi\n\nhello stdout", "text/markdown")},
            timeout=30.0,
        )
        assert resp.status_code == 200
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
    finally:
        if proc.poll() is None:  # 任何失败路径都确保杀干净
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    text = out.decode("utf-8", errors="replace")
    assert "ARRIVE" in text, text[-2000:]
    assert "DONE" in text, text[-2000:]
