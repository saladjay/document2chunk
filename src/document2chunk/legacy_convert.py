# src/document2chunk/legacy_convert.py
"""legacy_convert —— LibreOffice headless 归一化转换执行器（spec §3.2）。

每次转换独立 -env:UserInstallation profile（headless 全局锁，不隔离并发必死锁）；
超时 kill；错误映射：soffice 缺失→MissingDependencyError(503)、超时→TimeoutError(500)、
产物缺失→LegacyConversionError(422)。消息一律带输入扩展名（即检测出的格式）。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
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
    data: bytes, in_ext: str, target_ext: str, *, timeout: Optional[int] = None
) -> bytes:
    """转换 bytes：落盘 <uuid><in_ext> → soffice --convert-to <target_ext> → 读回。

    timeout=None 时用 DOCUMENT2CHUNK_SOFFICE_TIMEOUT（默认 120s）。
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
        try:
            subprocess.run(cmd, timeout=timeout, check=False,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"LibreOffice 转换超时（{timeout}s，可调 DOCUMENT2CHUNK_SOFFICE_TIMEOUT）"
                f"，输入格式 {in_ext}"
            ) from exc
        product = outdir / f"input{target_ext}"
        if not product.exists() or product.stat().st_size == 0:
            raise LegacyConversionError(
                f"LibreOffice 无法转换该文件（输入格式 {in_ext} → {target_ext}）"
            )
        return product.read_bytes()


def _norm(ext: str) -> str:
    return ext if ext.startswith(".") else "." + ext
