# src/document2chunk/format_detect.py
"""format_detect —— 内容指纹识别（规则层 + magika 惰性兜底）。

两级串联（spec §2.2）：规则层（魔数/zip 条目名/OLE2 流名/rtf 文本头，µs 级、
结构硬证据）命中即返回；未命中才惰性加载 magika（onnxruntime +40MB 禁止顶层
import）。识别只看内容不看文件名——错标件自动归位。任何输入不抛异常。
"""
from __future__ import annotations

import binascii
import io
import logging
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class FileKind(str, Enum):
    """内容指纹识别结果（独立于 ir.SourceType——老格式没有对应 SourceType）。"""

    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"
    PPTX = "pptx"
    ZIP = "zip"
    DOC = "doc"
    XLS = "xls"
    PPT = "ppt"
    RTF = "rtf"
    WPS = "wps"
    IMAGE = "image"
    MD = "md"
    TXT = "txt"
    UNKNOWN = "unknown"


_KIND_EXT = {
    FileKind.PDF: ".pdf", FileKind.DOCX: ".docx", FileKind.XLSX: ".xlsx",
    FileKind.PPTX: ".pptx", FileKind.ZIP: ".zip", FileKind.DOC: ".doc",
    FileKind.XLS: ".xls", FileKind.PPT: ".ppt", FileKind.RTF: ".rtf",
    FileKind.WPS: ".wps",
}

# OLE2 流名 → kind（含 WPS 文字常见流名；未命中流名的 OLE2 → WPS 兜底由 magika 层做）
_OLE2_STREAM_KIND = [
    ("WordDocument", FileKind.DOC),
    ("PowerPoint Document", FileKind.PPT),
    ("Workbook", FileKind.XLS),
]

_IMAGE_MAGICS = [
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"), (b"BM", "bmp"),
    (b"II*\x00", "tif"), (b"MM\x00*", "tif"), (b"GIF8", "gif"),
]


@dataclass
class Detection:
    kind: FileKind
    ext: str  # 规范扩展名（含点）；UNKNOWN 为 ""
    layer: str  # "rules" | "magika" | "none"


def detect_image(data: bytes) -> bool:
    """图片魔数判定（PNG/JPEG/BMP/TIFF/GIF/WebP 头两判定交给调用方路由 OCR）。"""
    for magic, _ in _IMAGE_MAGICS:
        if data[: len(magic)] == magic:
            return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _detect_by_zip(data: bytes) -> Optional[FileKind]:
    if data[:2] != b"PK":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
            names = set(zf.namelist())
    except Exception:  # noqa: BLE001 —— 非法 zip 本层不认领，落到 UNKNOWN（不抛）
        return None
    if any(n.startswith("word/") for n in names):
        return FileKind.DOCX
    if any(n.startswith("xl/") for n in names):
        return FileKind.XLSX
    if any(n.startswith("ppt/") for n in names):
        return FileKind.PPTX
    return FileKind.ZIP


def _detect_by_ole2(data: bytes) -> Optional[FileKind]:
    if data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        return None
    blob = bytes(data[: 1 << 20])  # 流名目录一般在前 1MB 内
    for stream, kind in _OLE2_STREAM_KIND:
        if stream.encode("utf-16-le") in blob:
            return kind
    return FileKind.WPS  # Kingsoft 变体：流名非标准 → 尽力按 wps 转 docx


def _rules_identify(data: bytes) -> Detection:
    """纯规则层识别。数据不足/乱字节 → UNKNOWN，绝不抛异常。"""
    data = bytes(data or b"")
    if data[:5] == b"%PDF-":
        return Detection(FileKind.PDF, ".pdf", "rules")
    if detect_image(data):
        return Detection(FileKind.IMAGE, ".png", "rules")  # ext 仅供诊断，路由走 OCR
    if data[:6].lstrip().startswith(b"{\\rtf"):
        return Detection(FileKind.RTF, ".rtf", "rules")
    zk = _detect_by_zip(data)
    if zk is not None:
        return Detection(zk, _KIND_EXT[zk], "rules")
    ok = _detect_by_ole2(data)
    if ok is not None:
        return Detection(ok, _KIND_EXT[ok], "rules")
    return Detection(FileKind.UNKNOWN, "", "rules")


_MAGIKA_OBJ = None  # 模型实例缓存（进程级，避免每次 5ms 加载）


def _magika_label(data: bytes) -> Optional[str]:
    """magika label；未安装/失败返回 None（绝不上抛）。惰性 import 纪律见模块 docstring。"""
    global _MAGIKA_OBJ
    try:
        if _MAGIKA_OBJ is None:
            import magika  # 顶层禁止 import（onnxruntime 40MB）
            _MAGIKA_OBJ = magika.Magika()
        return _MAGIKA_OBJ.identify_bytes(data).output.label  # 如 "wps"/"unknown"
    except Exception as e:  # noqa: BLE001
        logger.warning("magika 识别失败（跳过兜底）: %s: %s", type(e).__name__, e)
        return None


# magika label → FileKind（只列我们关心的；未知 label 忽略）
_MAGIKA_LABEL_KIND = {
    "doc": FileKind.DOC, "xls": FileKind.XLS, "ppt": FileKind.PPT,
    "docx": FileKind.DOCX, "xlsx": FileKind.XLSX, "pptx": FileKind.PPTX,
    "pdf": FileKind.PDF, "rtf": FileKind.RTF, "wps": FileKind.WPS,
}


def identify(data: bytes, use_magika: bool = True) -> Detection:
    """规则层优先；未命中且 use_magika → magika 兜底；都不中 → UNKNOWN。"""
    d = _rules_identify(data)
    if d.kind is not FileKind.UNKNOWN or not use_magika:
        return d
    label = _magika_label(bytes(data or b""))
    kind = _MAGIKA_LABEL_KIND.get(label or "")
    if kind is not None:
        return Detection(kind, _KIND_EXT.get(kind, ""), "magika")
    return Detection(FileKind.UNKNOWN, "", "none")


def diagnose(data: bytes, name: Optional[str] = None) -> str:
    """报错诊断串：文件名 + 魔数前 16 字节 hex + 两层识别意见。"""
    data = bytes(data or b"")
    head = binascii.hexlify(data[:16], "-").decode("ascii") or "(空)"
    d = identify(data)
    return (
        f"文件: {name or '(未命名)'}; 魔数: {head}; "
        f"规则层: {'未命中' if d.layer != 'rules' else d.kind.value}; "
        f"magika: {'未启用/未安装' if d.layer != 'magika' else d.kind.value}"
    )
