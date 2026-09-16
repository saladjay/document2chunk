"""legacy_convert —— LibreOffice headless 归一化转换执行器（spec §3.2）。

每次转换独立 -env:UserInstallation profile（headless 全局锁，不隔离并发必死锁）；
超时 killpg 杀整棵进程树（soffice→oosplash→soffice.bin）；错误映射：soffice 缺失→
MissingDependencyError(503)、超时→TimeoutError(500)、转换失败（rc!=0/产物缺失/0 字节）→
LegacyConversionError(422)。消息带文件名、扩展名、rc 与 stderr 尾巴。
"""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from document2chunk.exceptions import Document2ChunkError, MissingDependencyError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = int(os.environ.get("DOCUMENT2CHUNK_SOFFICE_TIMEOUT", "120"))


class LegacyConversionError(Document2ChunkError):
    """LibreOffice 转换失败（产物缺失/0 字节）。"""


def soffice_available() -> bool:
    return shutil.which("soffice") is not None


def convert(
    data: bytes,
    in_ext: str,
    target_ext: str,
    *,
    timeout: Optional[int] = None,
    name: Optional[str] = None,
) -> bytes:
    """转换 bytes：落盘 input<in_ext> → soffice --convert-to <target_ext> → 读回。

    timeout=None 时用 DOCUMENT2CHUNK_SOFFICE_TIMEOUT（默认 120s）；name 仅用于报错定位。
    """
    exe = shutil.which("soffice")
    if exe is None:
        raise MissingDependencyError(
            "LibreOffice 未安装（容器需 libreoffice-writer/impress + soffice 在 PATH）"
        )
    timeout = timeout if timeout is not None else _DEFAULT_TIMEOUT
    in_ext, target_ext = _norm(in_ext), _norm(target_ext)

    with tempfile.TemporaryDirectory(prefix="d2c_legacy_") as td:
        td_path = Path(td)
        src = td_path / f"input{in_ext}"
        outdir = td_path / "out"
        outdir.mkdir()
        src.write_bytes(data)
        profile = td_path / "profile"  # 独立 profile：并发/残留锁隔离
        cmd = [
            exe, "--headless", "--norestore",
            f"-env:UserInstallation=file://{profile.as_posix()}",
            "--convert-to", target_ext.lstrip("."),
            "--outdir", str(outdir), str(src),
        ]
        # start_new_session：超时按进程组杀树（soffice 脚本→oosplash→soffice.bin，
        # 只 SIGKILL 直接子进程会留孤儿 soffice.bin 持续烧 CPU/内存）
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError, OSError):
                proc.kill()  # 进程组已消失/平台无 killpg → 退回直接杀
            proc.wait(timeout=10)
            raise TimeoutError(
                f"LibreOffice 转换超时（{timeout}s，可调 DOCUMENT2CHUNK_SOFFICE_TIMEOUT）"
                f"，文件 {name or '(未命名)'}，输入格式 {in_ext}"
            ) from exc
        _out, stderr = proc.communicate()
        rc = proc.returncode
        stderr_tail = (stderr or b"")[-200:].decode("utf-8", errors="replace").strip()
        product = outdir / f"input{target_ext}"
        if rc != 0 or not product.exists() or product.stat().st_size == 0:
            raise LegacyConversionError(
                f"LibreOffice 无法转换：{name or '(未命名)'}"
                f"（{in_ext} → {target_ext}，rc={rc}）：{stderr_tail}"
            )
        return product.read_bytes()


def _norm(ext: str) -> str:
    return ext if ext.startswith(".") else "." + ext
