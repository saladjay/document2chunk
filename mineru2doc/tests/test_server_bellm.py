"""server bellm 引擎单测：TestClient + env monkeypatch（不真连网关）。"""
import io
import pathlib

import pytest
from fastapi.testclient import TestClient

import mineru2doc.server as server_mod
from mineru2doc.server import create_app


@pytest.fixture()
def bellm_env(monkeypatch):
    monkeypatch.setenv("MINERU_ENGINE", "bellm")
    monkeypatch.setenv("MINERU_BELLM_BASE_URL", "http://bellm:1/bellm/MinerU")
    monkeypatch.setenv("MINERU_BELLM_AUTH", "Basic a2V4")


def test_health_exposes_engine(bellm_env):
    r = TestClient(create_app()).get("/health")
    assert r.status_code == 200
    assert r.json()["engine"] == "bellm"


def test_default_engine_is_local(monkeypatch):
    monkeypatch.delenv("MINERU_ENGINE", raising=False)
    assert TestClient(create_app()).get("/health").json()["engine"] == "local"


def test_bellm_rejects_docx_with_400(bellm_env):
    r = TestClient(create_app()).post("/parse", files={
        "file": ("报告.docx", io.BytesIO(b"PK\x03\x04"), "application/octet-stream")})
    assert r.status_code == 400
    assert r.json()["status"] == "error"
    assert "PDF/PNG/JPG/JPEG" in r.json()["message"]


def test_bellm_accepts_pdf_passes_engine_kwargs(bellm_env, tmp_path, monkeypatch):
    captured = {}

    def fake_convert_to_dir(input_file, output_dir, image_dir=None, *, base_url=None,
                            engine=None, demote=False):
        captured.update(base_url=base_url, engine=engine, demote=demote)
        out = pathlib.Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        result = out / "result.md"
        result.write_text("# 桩\n\n正文", encoding="utf-8")
        return str(result)

    monkeypatch.setattr(server_mod, "convert_to_dir", fake_convert_to_dir)
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-1.4 stub")  # 路径模式先查文件存在，须真实落盘
    r = TestClient(create_app()).post("/parse", data={
        "file_path": str(src), "output_dir": str(tmp_path / "out")})
    assert captured["engine"] == "bellm"
    assert captured["base_url"] == "http://bellm:1/bellm/MinerU"
    assert r.json() == {"status": "ok"}


def test_bellm_without_auth_fails_fast(bellm_env, monkeypatch):
    monkeypatch.setenv("MINERU_BELLM_AUTH", "")
    with pytest.raises(RuntimeError, match="MINERU_BELLM_AUTH"):
        create_app()
