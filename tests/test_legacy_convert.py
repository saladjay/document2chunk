# tests/test_legacy_convert.py
"""legacy_convert：profile 隔离/超时杀树/错误诊断/soffice 缺失——Popen 全 mock。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from document2chunk import legacy_convert as lc
from document2chunk.exceptions import MissingDependencyError

DATA = b"fake doc bytes"


class _FakePopen:
    """Popen 桩：wait 写产物/抛超时，communicate 回 stderr，记录 kill 调用与 kwargs。"""

    last: "_FakePopen | None" = None

    def __init__(self, cmd, product=b"converted", rc=0, timeout=False, **kw):
        self.cmd = cmd
        self.popen_kwargs = kw
        self.pid = 4242
        self.returncode = rc
        self._product = product
        self._timeout = timeout
        self.direct_killed = False
        _FakePopen.last = self

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        if self._timeout:
            self._timeout = False  # 只首次超时：kill 后的收尸 communicate 正常返回
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        if self._product is not None:
            out = Path(self.cmd[self.cmd.index("--outdir") + 1])
            target = self.cmd[self.cmd.index("--convert-to") + 1]
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{Path(self.cmd[-1]).stem}.{target}").write_bytes(self._product)
        stderr = b"Error: source format not detected" if self.returncode else b""
        return b"", stderr

    def kill(self):
        self.direct_killed = True


def _patch_soffice(monkeypatch, **popen_kw):
    monkeypatch.setattr(lc.shutil, "which", lambda n: "/usr/bin/soffice")
    monkeypatch.setattr(
        lc.subprocess, "Popen", lambda cmd, **kw: _FakePopen(cmd, **{**popen_kw, **kw})
    )
    _FakePopen.last = None


def test_convert_builds_solo_profile_command(monkeypatch):
    """断言独立 UserInstallation profile + --convert-to + outdir + 产物读回。"""
    _patch_soffice(monkeypatch)
    got = lc.convert(DATA, ".doc", ".docx")
    assert got == b"converted"
    cmd = _FakePopen.last.cmd
    assert any(str(a).startswith("-env:UserInstallation=file://") for a in cmd)
    assert "--convert-to" in cmd and "docx" in cmd
    assert "--headless" in cmd and "--norestore" in cmd
    assert _FakePopen.last.popen_kwargs.get("start_new_session") is True  # 超时杀树的前提


def test_convert_soffice_missing_503(monkeypatch):
    monkeypatch.setattr(lc.shutil, "which", lambda n: None)
    with pytest.raises(MissingDependencyError):
        lc.convert(DATA, ".doc", ".docx")


def test_convert_timeout_kills_process_group(monkeypatch):
    """超时 → killpg 杀进程树 + TimeoutError 消息带文件名（Windows 无 killpg 则 shim）。"""
    calls = {}
    monkeypatch.setattr(lc.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(
        lc.os, "killpg", lambda pgid, sig: calls.setdefault("pgid", (pgid, sig)), raising=False
    )
    monkeypatch.setattr(lc.signal, "SIGKILL", 9, raising=False)
    _patch_soffice(monkeypatch, timeout=True, product=None)

    with pytest.raises(TimeoutError) as ei:
        lc.convert(DATA, ".doc", ".pdf", timeout=1, name="报告.doc")
    assert calls["pgid"] == (4242, 9)  # 进程组整树杀
    assert "报告.doc" in str(ei.value)
    assert ".doc" in str(ei.value)


def test_convert_timeout_fallback_direct_kill(monkeypatch):
    """killpg 不可用（平台/进程组消失）→ 退回 proc.kill()。"""
    monkeypatch.setattr(lc.os, "getpgid", lambda pid: pid, raising=False)

    def _no_such_group(pgid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(lc.os, "killpg", _no_such_group, raising=False)  # 强制走 except 分支
    monkeypatch.setattr(lc.signal, "SIGKILL", 9, raising=False)
    _patch_soffice(monkeypatch, timeout=True, product=None)

    with pytest.raises(TimeoutError):
        lc.convert(DATA, ".doc", ".pdf", timeout=1)
    assert _FakePopen.last.direct_killed


def test_convert_nonzero_rc_422_with_diagnostics(monkeypatch):
    """rc!=0 → LegacyConversionError 消息含文件名/rc/stderr 尾巴。"""
    _patch_soffice(monkeypatch, rc=2, product=None)

    with pytest.raises(lc.LegacyConversionError) as ei:
        lc.convert(DATA, ".wps", ".docx", name="文档.wps")
    msg = str(ei.value)
    assert "文档.wps" in msg
    assert "rc=2" in msg
    assert "source format not detected" in msg


def test_convert_missing_product_422(monkeypatch):
    """soffice 返回 0 但产物不存在 → LegacyConversionError（422）。"""
    _patch_soffice(monkeypatch, product=None)

    with pytest.raises(lc.LegacyConversionError) as ei:
        lc.convert(DATA, ".doc", ".docx", name="a.doc")
    assert "a.doc" in str(ei.value)
    assert "rc=0" in str(ei.value)


def test_convert_real_soffice(tmp_path):
    """真 soffice 环境集成用例；无环境自动跳过。"""
    if not lc.soffice_available():
        pytest.skip("soffice 未安装")
    rtf = rb"{\rtf1\ansi Real integration test document.\par}"
    got = lc.convert(rtf, ".rtf", ".docx", timeout=60)
    assert got[:2] == b"PK"  # docx 是 zip 容器


def test_container_env_complete():
    """容器内 legacy extra 与 soffice 双就绪；裸机无环境跳过。"""
    try:
        import magika  # noqa: F401
        import olefile  # noqa: F401
    except ImportError:
        pytest.skip("legacy extra 未安装")
    if not lc.soffice_available():
        pytest.skip("soffice 未安装")
