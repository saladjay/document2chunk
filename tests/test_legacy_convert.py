# tests/test_legacy_convert.py
"""legacy_convert：profile 隔离/超时/产物缺失/soffice 缺失——subprocess 全 mock。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from document2chunk import legacy_convert as lc
from document2chunk.exceptions import MissingDependencyError

DATA = b"fake doc bytes"


class _FakeCompleted:
    returncode = 0


def test_convert_builds_solo_profile_command(tmp_path):
    """断言独立 UserInstallation profile + --convert-to + outdir。"""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        # 产物须写在实现读取的位置：--outdir 后的目录 + 源文件名换目标扩展名
        out = Path(cmd[cmd.index("--outdir") + 1])
        src = Path(cmd[-1])
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{src.stem}.docx").write_bytes(b"converted")
        return _FakeCompleted()

    with patch.object(lc, "subprocess") as m_sub, patch.object(
        lc, "shutil", which=lambda n: "/usr/bin/soffice"
    ):
        m_sub.run = fake_run
        m_sub.TimeoutExpired = subprocess.TimeoutExpired
        got = lc.convert(DATA, ".doc", ".docx")
    assert got == b"converted"
    cmd = captured["cmd"]
    assert any(str(a).startswith("-env:UserInstallation=file://") for a in cmd)
    assert "--convert-to" in cmd and "docx" in cmd


def test_convert_soffice_missing_503():
    with patch.object(lc, "shutil", which=lambda n: None):
        with pytest.raises(MissingDependencyError):
            lc.convert(DATA, ".doc", ".docx")


def test_convert_timeout_raises_timeouterror():
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    with patch.object(lc, "subprocess") as m_sub, patch.object(
        lc, "shutil", which=lambda n: "/usr/bin/soffice"
    ):
        m_sub.run = fake_run
        m_sub.TimeoutExpired = subprocess.TimeoutExpired
        with pytest.raises(TimeoutError):
            lc.convert(DATA, ".doc", ".pdf", timeout=1)


def test_convert_missing_product_422(tmp_path):
    """soffice 返回 0 但产物不存在 → LegacyConversionError（422）。"""

    def fake_run(cmd, **kw):
        return _FakeCompleted()  # 不写产物

    with patch.object(lc, "subprocess") as m_sub, patch.object(
        lc, "shutil", which=lambda n: "/usr/bin/soffice"
    ):
        m_sub.run = fake_run
        m_sub.TimeoutExpired = subprocess.TimeoutExpired
        with pytest.raises(lc.LegacyConversionError):
            lc.convert(DATA, ".doc", ".docx")


def test_convert_real_soffice(tmp_path):
    """真 soffice 环境集成用例；无环境自动跳过。"""
    if not lc.soffice_available():
        pytest.skip("soffice 未安装")
    rtf = rb"{\rtf1\ansi Real integration test document.\par}"
    got = lc.convert(rtf, ".rtf", ".docx", timeout=60)
    assert got[:2] == b"PK"  # docx 是 zip 容器
