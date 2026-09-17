"""S-2:PDF 提取子进程看门狗的 TDD 测试(issues7 B 类)。

楔死点不只在 detect——提取期 MuPDF C 调用同样可能被病态件楔死线程。
防线:提取整体子进程化(env DOCUMENT2CHUNK_EXTRACT_TIMEOUT 默认 600s,0=关),
超时 SIGKILL → InvalidSourceError,服务存活。等价:同代码同输入,序列化往返保真。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from _fixtures import make_simple_pdf


def test_red_worker_entry_roundtrip(tmp_path):
    """子进程入口:提取→ExtractionResult JSON 落盘→父进程回载。"""
    from document2chunk.extractors.pdf import PdfExtractor
    from document2chunk.pipeline.pdf_extract_worker import main as worker_main

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_simple_pdf())
    img = tmp_path / "img"
    out = tmp_path / "out.json"
    rc = worker_main([sys.executable, str(pdf), str(img), str(out), "skip_detect"])
    assert rc == 0
    direct = PdfExtractor(skip_detect=True).extract(str(pdf))
    loaded = __import__("document2chunk.ir", fromlist=["ExtractionResult"]).ExtractionResult.model_validate_json(
        out.read_text(encoding="utf-8")
    )
    assert loaded.model_dump_json() == direct.model_dump_json(), "序列化往返必须保真"


def test_red_guarded_extract_equivalent(tmp_path):
    """guarded 子进程提取与进程内直提结果逐字节一致。"""
    from document2chunk.extractors.pdf import PdfExtractor
    from document2chunk.pipeline.pdf_extract_worker import extract_pdf_guarded

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_simple_pdf())
    img_g = tmp_path / "img_g"
    img_d = tmp_path / "img_d"
    guarded = extract_pdf_guarded(str(pdf), str(img_g), 120, skip_detect=True)
    direct = PdfExtractor(image_dir=str(img_d), skip_detect=True).extract(str(pdf))
    assert guarded.model_dump_json() == direct.model_dump_json()


def test_red_guarded_timeout_raises(tmp_path, monkeypatch):
    """子进程被测试钩子拖住 → 超时抛 InvalidSourceError(快速返回,不挂死)。"""
    from document2chunk.exceptions import InvalidSourceError
    from document2chunk.pipeline.pdf_extract_worker import extract_pdf_guarded

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_simple_pdf())
    monkeypatch.setenv("DOCUMENT2CHUNK_EXTRACT_TEST_SLEEP", "30")
    t0 = time.perf_counter()
    with pytest.raises(InvalidSourceError, match="疑似病态 PDF"):
        extract_pdf_guarded(str(pdf), str(tmp_path / "img"), 3, skip_detect=True)
    assert time.perf_counter() - t0 < 15


def test_red_serve_wiring_uses_guard(tmp_path, monkeypatch):
    """serve PDF 提取默认走 guarded(env 默认 600s)。"""
    import document2chunk.pipeline.pdf_extract_worker as w
    from document2chunk import serve

    calls = []
    real = w.extract_pdf_guarded

    def counting(source, image_dir, timeout_s, *, skip_detect):
        calls.append(timeout_s)
        return real(source, image_dir, timeout_s, skip_detect=skip_detect)

    monkeypatch.setattr(w, "extract_pdf_guarded", counting)
    monkeypatch.setattr(serve, "_extract_with_images", serve._extract_with_images)

    p = tmp_path / "a.pdf"
    p.write_bytes(make_simple_pdf())
    img = tmp_path / "img"
    out = tmp_path / "out"
    img.mkdir()
    out.mkdir()
    serve.parse_to_files(str(p), str(out), str(img))
    assert calls, "serve PDF 提取应走 guarded 子进程"


def test_guard_extract_timeout_zero_disables(tmp_path, monkeypatch):
    """DOCUMENT2CHUNK_EXTRACT_TIMEOUT=0 → 退回进程内直提(兼容开关)。"""
    import document2chunk.extractors.pdf as pdf_mod
    from document2chunk import serve

    calls = []
    real = pdf_mod.PdfExtractor.extract

    def counting(self, source, **kw):
        calls.append(1)
        return real(self, source, **kw)

    monkeypatch.setattr(pdf_mod.PdfExtractor, "extract", counting)
    monkeypatch.setenv("DOCUMENT2CHUNK_EXTRACT_TIMEOUT", "0")

    p = tmp_path / "a.pdf"
    p.write_bytes(make_simple_pdf())
    img = tmp_path / "img"
    out = tmp_path / "out"
    img.mkdir()
    out.mkdir()
    serve.parse_to_files(str(p), str(out), str(img))
    assert calls == [1], "timeout=0 应进程内直提"


def test_guard_worker_subprocess_cli(tmp_path):
    """python -m 子进程形式自检(父进程实际调用形态)。"""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_simple_pdf())
    img = tmp_path / "img"
    out = tmp_path / "out.json"
    r = subprocess.run(
        [sys.executable, "-m", "document2chunk.pipeline.pdf_extract_worker",
         str(pdf), str(img), str(out), "skip_detect"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr[-200:]
    last_line = [l for l in r.stdout.strip().splitlines() if l.strip()][-1]
    assert json.loads(last_line) == {"ok": True}
