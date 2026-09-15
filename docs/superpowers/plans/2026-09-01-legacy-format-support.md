# 老格式 Office 输入支持 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** /parse-pdf 识别所有 Office 老格式（doc/rtf/wps/ppt/pptx 及错标件），LibreOffice 归一化转换为现代格式后走现有解析管线；xls/xlsx 明确 400；不可识别输入报错带文件名+指纹诊断。

**Architecture:** 扩展名优先快路径（现有请求零扰动）→ 老格式后缀/格式类异常时进入指纹识别（手搓 zipfile+olefile 规则层 → magika 惰性兜底两级串联）→ 前置归一化转换（容器内 soffice，独立 profile）→ 转换产物回现有管线。不新增 SourceType、不动 extractor/postprocess。

**Tech Stack:** olefile（纯 Python OLE2 流名读取）、magika 1.0.3（onnxruntime 兜底，惰性 import）、LibreOffice headless（容器内转换）、FastAPI 现有 exception handler 体系。

**Spec:** `docs/superpowers/specs/2026-09-01-legacy-format-support-design.md`

## Global Constraints

- 现有格式（pdf/docx/md/txt/图片）行为**零变化**——回归门禁：改动前后产物 diff 一致
- 指纹识别只在「老格式后缀」或「格式类异常」（UnsupportedFormatError / InvalidDocxError / InvalidSourceError 等 Document2ChunkError 打开·解包期失败）时触发，不捕内容解析错误
- magika 顶层禁止 import（onnxruntime 40MB 只能惰性加载）；RTF 由规则层 `{\rtf` 截胡（magika 实测误判 txt）
- soffice 调用必须独立 UserInstallation profile（headless 全局锁）；超时默认 120s 可配
- 报错消息必须带**文件名 + 检测到的格式**，禁止「不支持的源格式：bytes」式谜语
- 所有报错走现有 exception handler 映射：UnsupportedFormatError→400、MissingDependencyError→503、其余 Document2ChunkError→422（`_chai_parse` 大捕全需放行 Document2ChunkError）
- xls/xlsx → 400「表格格式即将支持，请导出 PDF/docx 后上传」
- 测试默认 mock soffice/magika；真实环境用例 skipif 守卫
- 提交信息格式沿用仓库惯例（`feat(scope): 中文描述`），以 `Co-Authored-By: Claude <noreply@anthropic.com>` 结尾

---

### Task 1: format_detect 规则层——内容指纹识别器

**Files:**
- Create: `src/document2chunk/format_detect.py`
- Test: `tests/test_format_detect.py`

**Interfaces:**
- Consumes: 无（独立模块，仅标准库 + olefile 可选）
- Produces:
  - `FileKind(str, Enum)`：`PDF` `DOCX` `XLSX` `PPTX` `ZIP` `DOC` `XLS` `PPT` `RTF` `WPS` `IMAGE` `MD` `TXT` `UNKNOWN`
  - `Detection` dataclass：`kind: FileKind`、`ext: str`（规范扩展名如 ".doc"，UNKNOWN 为 ""）、`layer: str`（"rules" | "magika" | "none"）
  - `identify(data: bytes) -> Detection`——纯规则层（本任务），任何输入不抛异常
  - `detect_image(data: bytes) -> bool`（魔数判定 png/jpeg/bmp/tiff/gif/webp）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_format_detect.py
"""format_detect 规则层：魔数/zip 条目/OLE2 流名/rtf 全矩阵 + 错标件 + 脏输入。"""

from __future__ import annotations

import io
import zipfile

from document2chunk.format_detect import FileKind, identify

PDF_BYTES = b"%PDF-1.7\nfake pdf body"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 32
RTF_BYTES = rb"{\rtf1\ansi\ansicpg936 \u25105\u26159 RTF \u27491\u25991}"
RANDOM_BYTES = bytes(range(256)) * 4  # 无任何已知魔数


def _minimal_zip(entries: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in entries:
            z.writestr(n, b"x")
    return buf.getvalue()


# 手工 OLE2：真实文件头 + 目录扇区放 UTF-16LE 流名（规则层原始扫描即可命中）
def _fake_ole2(stream_name: str) -> bytes:
    head = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 56
    return head + stream_name.encode("utf-16-le") + b"\x00" * 64


def test_pdf_magic():
    assert identify(PDF_BYTES).kind == FileKind.PDF

def test_image_magics():
    assert identify(PNG_BYTES).kind == FileKind.IMAGE
    assert identify(JPEG_BYTES).kind == FileKind.IMAGE

def test_zip_family():
    assert identify(_minimal_zip(["word/document.xml", "[Content_Types].xml"])).kind == FileKind.DOCX
    assert identify(_minimal_zip(["xl/workbook.xml"])).kind == FileKind.XLSX
    assert identify(_minimal_zip(["ppt/presentation.xml"])).kind == FileKind.PPTX
    assert identify(_minimal_zip(["a.txt", "b/c.txt"])).kind == FileKind.ZIP

def test_ole2_family():
    assert identify(_fake_ole2("WordDocument")).kind == FileKind.DOC
    assert identify(_fake_ole2("Workbook")).kind == FileKind.XLS
    assert identify(_fake_ole2("PowerPoint Document")).kind == FileKind.PPT

def test_rtf_rules_layer_not_magika():
    d = identify(RTF_BYTES)
    assert d.kind == FileKind.RTF
    assert d.layer == "rules"  # RTF 定死规则层（magika 误判 txt）

def test_mislabeled_docx_named_but_ignored():
    """错标件：.docx 后缀 OLE2 内容——identify 只看内容，kind == DOC。"""
    d = identify(_fake_ole2("WordDocument"))
    assert d.kind == FileKind.DOC
    assert d.ext == ".doc"

def test_unknown_safe():
    for dirty in (b"", RANDOM_BYTES, b"\x00" * 8, b"PK\x03"):  # 截断/空/半魔数
        assert identify(dirty).kind == FileKind.UNKNOWN
        assert identify(dirty).ext == ""

def test_detection_ext_mapping():
    assert identify(PDF_BYTES).ext == ".pdf"
    assert identify(_minimal_zip(["word/document.xml"])).ext == ".docx"
    assert identify(RTF_BYTES).ext == ".rtf"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_format_detect.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'document2chunk.format_detect'`

- [ ] **Step 3: 最小实现**

```python
# src/document2chunk/format_detect.py
"""format_detect —— 内容指纹识别（规则层 + magika 惰性兜底）。

两级串联（spec §2.2）：规则层（魔数/zip 条目名/OLE2 流名/rtf 文本头，µs 级、
结构硬证据）命中即返回；未命中才惰性加载 magika（onnxruntime +40MB 禁止顶层
import）。识别只看内容不看文件名——错标件自动归位。任何输入不抛异常。
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    except Exception:  # noqa: BLE001 —— 非法 zip 当 UNKNOWN，不抛
        return FileKind.ZIP
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


def identify(data: bytes) -> Detection:
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
```

注：`identify` 本任务**不调用 magika**（Task 2 加兜底层，签名不变）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_format_detect.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/document2chunk/format_detect.py tests/test_format_detect.py
git commit -m "feat(detect): 内容指纹识别规则层——魔数/zip条目/OLE2流名/rtf"
```

---

### Task 2: magika 惰性兜底层 + 诊断信息

**Files:**
- Modify: `src/document2chunk/format_detect.py`
- Modify: `pyproject.toml`（加 `legacy` extra）
- Test: `tests/test_format_detect.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `Detection`/`FileKind`/`identify`
- Produces:
  - `identify(data: bytes, use_magika: bool = True) -> Detection`——签名扩展：规则层未命中且 `use_magika=True` 时走 magika
  - `diagnose(data: bytes, name: Optional[str] = None) -> str`——报错附带的诊断串（文件名 + 魔数 hex + 两层识别意见），供 serve 层拼进错误消息
  - pyproject：`legacy = ["olefile>=0.46", "magika>=1.0.3"]` extra

- [ ] **Step 1: 追加失败测试**

```python
# tests/test_format_detect.py 追加
import sys
from unittest.mock import MagicMock

import document2chunk.format_detect as fd


def test_magika_fallback_adopted(monkeypatch):
    """规则层未命中 → magika label 非 unknown/zip → 采纳为 kind。"""
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)  # 重置模型缓存（防测试间泄漏）
    fake = MagicMock()
    fake.Magika.return_value.identify_bytes.return_value.output.label = "wps"
    monkeypatch.setitem(sys.modules, "magika", fake)
    d = fd.identify(RANDOM_BYTES)
    assert d.kind == FileKind.WPS
    assert d.layer == "magika"


def test_magika_unknown_abstains(monkeypatch):
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)
    fake = MagicMock()
    fake.Magika.return_value.identify_bytes.return_value.output.label = "unknown"
    monkeypatch.setitem(sys.modules, "magika", fake)
    assert fd.identify(RANDOM_BYTES).layer == "none"


def test_magika_import_error_graceful(monkeypatch):
    """未安装 magika → 规则层结果原样返回，不抛。"""
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)
    monkeypatch.setitem(sys.modules, "magika", None)  # import 触发 ImportError
    assert fd.identify(PDF_BYTES).kind == FileKind.PDF
    assert fd.identify(RANDOM_BYTES).layer == "none"


def test_diagnose_message_contains_name_and_hex():
    msg = fd.diagnose(RANDOM_BYTES, "报告.docx")
    assert "报告.docx" in msg
    assert "00-01-02" in msg  # 魔数前字节 hex


def test_magika_model_cached(monkeypatch):
    """Magika 实例只构建一次（模型 5ms/次，缓存避免重复加载）。"""
    fake = MagicMock()
    fake.Magika.return_value.identify_bytes.return_value.output.label = "unknown"
    monkeypatch.setitem(sys.modules, "magika", fake)
    monkeypatch.setattr(fd, "_MAGIKA_OBJ", None)  # 重置缓存
    fd.identify(RANDOM_BYTES)
    fd.identify(RANDOM_BYTES)
    assert fake.Magika.call_count == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_format_detect.py -v -k magika_or_diagnose`
Expected: FAIL `AttributeError: ... has no attribute 'diagnose'`（use_magika 参数不存在）

- [ ] **Step 3: 实现 magika 兜底 + diagnose**

`format_detect.py` 追加/修改：

```python
import binascii
import logging

logger = logging.getLogger(__name__)

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


实现要求：把 Task 1 的 `identify` 函数**整体改名为 `_rules_identify`**（def 行改名，函数体零变化），Task 1 测试全部经新 `identify` 入口，自动兼容。


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
```

实现要点：Task 1 的 `identify` 函数体改名为 `_rules_identify`（逻辑零变化），新 `identify` 做串联调度。`test_unknown_safe` 等旧用例自动兼容（未装 magika 的 CI 环境 `_magika_label` 返回 None → layer="none"）。

`pyproject.toml` `[project.optional-dependencies]` 追加：

```toml
legacy = ["olefile>=0.46", "magika>=1.0.3"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_format_detect.py -v`
Expected: 全部 PASS（含 Task 1 旧用例）

- [ ] **Step 5: 提交**

```bash
git add src/document2chunk/format_detect.py tests/test_format_detect.py pyproject.toml
git commit -m "feat(detect): magika 惰性兜底 + diagnose 诊断串 + legacy extra"
```

---

### Task 3: legacy_convert——soffice 归一化转换执行器

**Files:**
- Create: `src/document2chunk/legacy_convert.py`
- Test: `tests/test_legacy_convert.py`

**Interfaces:**
- Consumes: 无（subprocess + 临时目录）
- Produces:
  - `LegacyConversionError(Document2ChunkError)`——转换失败/产物缺失（→422）
  - `convert(data: bytes, in_ext: str, target_ext: str, *, timeout: Optional[int] = None) -> bytes`
    - `in_ext`/`target_ext` 形如 ".doc"/".docx"（含点）
    - soffice 不存在 → `MissingDependencyError`（→503）；超时 → `TimeoutError`（→500，由 `_chai_parse` 兜底）；产物缺失/0 字节 → `LegacyConversionError`（→422）
  - `soffice_available() -> bool`（测试/健康检查用）

- [ ] **Step 1: 写失败测试（mock subprocess，不依赖真 soffice）**

```python
# tests/test_legacy_convert.py
"""legacy_convert：profile 隔离/超时/产物缺失/soffice 缺失——subprocess 全 mock。"""

from __future__ import annotations

import subprocess
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
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        (out / "x.docx").write_bytes(b"converted")
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

    with patch.object(lc, "shutil", which=lambda n: "/usr/bin/soffice"):
        with pytest.raises(TimeoutError):
            lc.convert(DATA, ".doc", ".pdf", timeout=1)


def test_convert_missing_product_422(tmp_path):
    """soffice 返回 0 但产物不存在 → LegacyConversionError（422）。"""

    def fake_run(cmd, **kw):
        return _FakeCompleted()  # 不写产物

    with patch.object(lc, "shutil", which=lambda n: "/usr/bin/soffice"):
        with pytest.raises(lc.LegacyConversionError):
            lc.convert(DATA, ".doc", ".docx")


def test_convert_real_soffice(tmp_path):
    """真 soffice 环境集成用例；无环境自动跳过。"""
    if not lc.soffice_available():
        pytest.skip("soffice 未安装")
    rtf = rb"{\rtf1\ansi Real integration test document.\par}"
    got = lc.convert(rtf, ".rtf", ".docx", timeout=60)
    assert got[:2] == b"PK"  # docx 是 zip 容器
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_legacy_convert.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'document2chunk.legacy_convert'`

- [ ] **Step 3: 实现**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_legacy_convert.py -v`
Expected: 前 5 个 PASS；`test_convert_real_soffice` 本机 SKIP（无 soffice）

- [ ] **Step 5: 提交**

```bash
git add src/document2chunk/legacy_convert.py tests/test_legacy_convert.py
git commit -m "feat(legacy): LibreOffice 归一化转换执行器——独立 profile/超时/错误映射"
```

---

### Task 4: serve 接线——老格式快路径 + 指纹兜底 + 错误放行

**Files:**
- Modify: `src/document2chunk/serve.py`（parse_to_files / parse_to_zip / 新私有助手）
- Modify: `src/document2chunk/api.py:376-394`（`_chai_parse` 异常放行）
- Test: `tests/test_serve_legacy.py`（新文件）

**Interfaces:**
- Consumes: Task 1/2 `format_detect.identify/diagnose/FileKind`；Task 3 `legacy_convert.convert`
- Produces（serve 层内部助手，供两模式共用）:
  - `_LEGACY_EXTS = {".doc", ".rtf", ".wps", ".ppt", ".pptx"}`、`_SPREADSHEET_EXTS = {".xls", ".xlsx", ".xlsm", ".et"}`
  - `_doc_target_ext() -> str`——`DOCUMENT2CHUNK_DOC_TARGET`（docx|pdf，默认 docx）→ ".docx"|".pdf"
  - `_legacy_convert(data, in_ext, target, name=None) -> bytes`——`legacy_convert.convert` 的间接引用层（测试 mock 点）
  - `_normalize_legacy(data: bytes, name, *, exc_prefix="") -> tuple[bytes, str]`——指纹识别→归一化：xls/xlsx 400；UNKNOWN 400+诊断；DOC/RTF/WPS→转换（目标 `_doc_target_ext()`）；PPT/PPTX→转 .pdf；ZIP 容器按内部条目改名归位（反向错标不转换）；PDF/DOCX/IMAGE 原样返回
  - `_prepare(source, name_hint) -> tuple[object, Optional[str]]`——后缀快路径决策入口，老格式后缀读 bytes 后调 `_normalize_legacy`
  - `_needs_legacy(name) -> bool`——表格后缀抛 400，老格式后缀返回 True
  - 兜底触发集 `_FORMAT_EXC_TYPES = (Document2ChunkError,)`——InvalidDocxError/InvalidSourceError 都是其后子类，全部兜底（exceptions.py 设计：内容级局部失败走 WARN+跳过不抛异常，能抛到这层的都是打开·解包期失败）

- [ ] **Step 1: 写失败集成测试**

```python
# tests/test_serve_legacy.py
"""serve 层老格式接线：后缀快路径、指纹兜底、xls 400、错误带文件名。

soffice/magika 全 mock（不依赖环境）；转换产物用最小 docx（test_docx.make_docx）。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from document2chunk import serve
from document2chunk.exceptions import UnsupportedFormatError

from test_docx import make_docx
from test_format_detect import RANDOM_BYTES, RTF_BYTES, _fake_ole2, _minimal_zip

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOC_XML = (
    f'<w:document xmlns:w="{W}"><w:body>'
    "<w:p><w:r><w:t>转换后正文</w:t></w:r></w:p>"
    "</w:body></w:document>"
)
CONVERTED_DOCX = make_docx(DOC_XML)


def _real_pdf(text: str = "演示转换产物") -> bytes:
    """真最小 PDF（pymupdf 生成）——假 PDF 会触发 _pdf_kind 真检测，必须用真的。"""
    import pymupdf

    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 72), text)
    data = d.tobytes()
    d.close()
    return data


def _read_md(zip_bytes: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return z.read("result.md").decode("utf-8")


def _patch_convert(monkeypatch, product: bytes = CONVERTED_DOCX):
    """mock legacy_convert.convert，记录 (in_ext, target)。"""
    calls = []
    monkeypatch.setattr(
        serve, "_legacy_convert",  # serve 内的间接引用点（见 Step 3）
        lambda data, in_ext, target, name=None: (
            calls.append((in_ext, target)) or product
        ),
    )
    return calls


def test_mislabeled_docx_zip_mode(monkeypatch):
    """错标件：filename=.docx 内容 OLE2 → docx 路解析失败 → 指纹兜底 → 转换 → 200。"""
    calls = _patch_convert(monkeypatch)
    md = _read_md(serve.parse_to_zip(_fake_ole2("WordDocument"), "报告.docx"))
    assert "转换后正文" in md
    assert calls and calls[0] == (".doc", ".docx")


def test_doc_suffix_direct_no_exception_first(monkeypatch):
    """真 .doc 后缀：不先错一次，直接转换分支。"""
    calls = _patch_convert(monkeypatch)
    md = _read_md(serve.parse_to_zip(_fake_ole2("WordDocument"), "报告.doc"))
    assert "转换后正文" in md
    assert calls[0] == (".doc", ".docx")


def test_rtf_suffix(monkeypatch):
    calls = _patch_convert(monkeypatch)
    serve.parse_to_zip(RTF_BYTES, "a.rtf")
    assert calls[0] == (".rtf", ".docx")


def test_ppt_suffix_targets_pdf(monkeypatch):
    """ppt → 统一转 PDF：转换目标 .pdf，产物真 PDF 进 PdfExtractor 出正文。"""
    calls = _patch_convert(monkeypatch, product=_real_pdf())
    md = _read_md(serve.parse_to_zip(_fake_ole2("PowerPoint Document"), "讲.ppt"))
    assert calls[0] == (".ppt", ".pdf")
    assert "演示转换产物" in md
```

```python
def test_docx_renamed_to_doc_no_conversion(monkeypatch):
    """反向错标：真 docx 内容但 .doc 后缀 → 识别归位不转换（PK 嗅探直接走 docx 解析）。"""
    calls = _patch_convert(monkeypatch)
    md = _read_md(serve.parse_to_zip(CONVERTED_DOCX, "a.doc"))
    assert calls == []  # 零转换
    assert "转换后正文" in md


def test_xlsx_suffix_400_with_name():
    with pytest.raises(UnsupportedFormatError) as ei:
        serve.parse_to_zip(_minimal_zip(["xl/workbook.xml"]), "表.xlsx")
    assert "表.xlsx" in str(ei.value)
    assert "即将支持" in str(ei.value)


def test_xls_by_fingerprint_400():
    """无后缀裸 OLE2-xls bytes → 规则层命中 XLS → 400 即将支持（规则层命中不走 magika）。"""
    with pytest.raises(UnsupportedFormatError) as ei:
        serve.parse_to_zip(_fake_ole2("Workbook"))
    assert "即将支持" in str(ei.value)


def test_unknown_bytes_error_has_filename_and_diagnosis():
    with pytest.raises(UnsupportedFormatError) as ei:
        serve.parse_to_zip(RANDOM_BYTES, "怪文件.xyz")
    assert "怪文件.xyz" in str(ei.value)
    assert "规则层" in str(ei.value)  # diagnose 串已拼入


def test_doc_target_env_pdf(monkeypatch):
    """DOCUMENT2CHUNK_DOC_TARGET=pdf → doc 转 pdf。"""
    calls = _patch_convert(monkeypatch, product=_real_pdf())
    monkeypatch.setenv("DOCUMENT2CHUNK_DOC_TARGET", "pdf")
    serve.parse_to_zip(_fake_ole2("WordDocument"), "报告.doc")
    assert calls[0] == (".doc", ".pdf")


def test_path_mode_mislabeled(tmp_path, monkeypatch):
    """路径模式同接线：错标 .docx → 兜底转换 → result.md。"""
    calls = _patch_convert(monkeypatch)
    src = tmp_path / "错标.docx"
    src.write_bytes(_fake_ole2("WordDocument"))
    out, imgs = tmp_path / "out", tmp_path / "images"
    serve.parse_to_files(str(src), str(out), str(imgs))
    assert "转换后正文" in (out / "result.md").read_text(encoding="utf-8")
    assert calls[0] == (".doc", ".docx")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_serve_legacy.py -v`
Expected: FAIL `AttributeError: module 'document2chunk.serve' has no attribute '_legacy_convert'`

- [ ] **Step 3: serve.py 接线实现**

`serve.py` 顶部 import 区追加：

```python
from document2chunk.exceptions import (
    Document2ChunkError,
    UnsupportedFormatError,  # 原 import 保留
)
```

模块级追加（`_txt_to_utf8` 之后）：

```python
# ---- 老格式归一化（spec docs/superpowers/specs/2026-09-01-legacy-format-support） ----
_LEGACY_EXTS = {".doc", ".rtf", ".wps", ".ppt", ".pptx"}  # 后缀即进转换分支
_SPREADSHEET_EXTS = {".xls", ".xlsx", ".xlsm", ".et"}  # 表格：二期，明确 400
_FORMAT_EXC_TYPES = (Document2ChunkError,)  # 打开·解包期异常（含 UnsupportedFormat/InvalidDocx/InvalidSource）

# 转换的间接引用点（测试 mock 用；包一层避免 monkeypatch 打不到惰性 import）
def _legacy_convert(data, in_ext, target, name=None):
    from document2chunk import legacy_convert
    logger.info("老格式归一化: %s %s→%s", name or "(未命名)", in_ext, target)
    return legacy_convert.convert(data, in_ext, target)


def _doc_target_ext() -> str:
    t = os.environ.get("DOCUMENT2CHUNK_DOC_TARGET", "docx").strip().lower()
    return ".pdf" if t == "pdf" else ".docx"


def _xls_err(name: Optional[str], kind: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(
        f"检测到表格格式（{kind}）：{name or '(未命名)'}——即将支持；"
        "请先导出为 PDF/docx 后上传"
    )


def _zip_container_ext(data: bytes) -> Optional[str]:
    """zip 容器内部类型 → 规范扩展名（反向错标归位用）；非 zip/纯 zip → None。"""
    from document2chunk.format_detect import FileKind, identify

    kind = identify(data).kind
    return {FileKind.DOCX: ".docx", FileKind.PPTX: ".pptx", FileKind.XLSX: ".xlsx"}.get(kind)


def _normalize_legacy(data: bytes, name: Optional[str], *, exc_prefix: str = "") -> tuple[bytes, str]:
    """指纹识别 → 归一化。返回 (产物 bytes, 归位后的规范名)。

    - XLS/XLSX → 400 即将支持
    - UNKNOWN → 400 + 诊断（exc_prefix 非空时拼原异常前缀）
    - DOC/RTF/WPS → 转换为 _doc_target_ext()；PPT/PPTX → 转换为 .pdf
    - ZIP 容器（反向错标：docx 改名 .doc）→ 按内部条目改名归位，不转换
    - PDF/DOCX/IMAGE 可解析 kind → 原样返回（按识别扩展名归位名）
    """
    from document2chunk.format_detect import FileKind, diagnose, identify

    d = identify(data)
    kind = d.kind
    if kind in (FileKind.XLS, FileKind.XLSX):
        raise _xls_err(name, kind.value)
    if kind is FileKind.UNKNOWN:
        head = f"{exc_prefix}——" if exc_prefix else ""
        raise UnsupportedFormatError(f"{head}无法识别的文件格式——{diagnose(data, name)}")
    if kind in (FileKind.PPT, FileKind.PPTX):
        target = ".pdf"
    elif kind in (FileKind.DOC, FileKind.RTF, FileKind.WPS):
        target = _doc_target_ext()
    elif kind is FileKind.ZIP:
        inner = _zip_container_ext(data)
        if inner is None:  # 纯 zip：无解析路径，给明确报错
            raise UnsupportedFormatError(
                f"检测到普通 zip 压缩包：{name or '(未命名)'}——请解压后上传其中的文档"
            )
        return data, (Path(name or "f").stem + inner)  # 反向错标：改名归位不转换
    else:  # PDF / DOCX / IMAGE：内容可解析，原异常另有原因——原样返回
        return data, name or (f"input{d.ext}" if d.ext else "input.bin")
    product = _legacy_convert(data, d.ext, target, name)
    return product, (Path(name or "f").stem + target)


def _needs_legacy(name: Optional[str]) -> bool:
    """后缀决策：True=老格式直转分支；另有表格后缀在此抛 400。"""
    suffix = Path(name).suffix.lower() if name else ""
    if suffix in _SPREADSHEET_EXTS:
        raise _xls_err(name, suffix.lstrip("."))
    return suffix in _LEGACY_EXTS


def _prepare(source, name_hint: Optional[str]) -> tuple[object, Optional[str]]:
    """统一入口：后缀快路径 + 老格式直转。返回 (新source, 新name)。

    bytes/路径两模式共用同一决策（先取 bytes 再识别）；现代格式原样返回零扰动。
    """
    if isinstance(source, (bytes, bytearray)):
        name, data = name_hint, bytes(source)
    else:
        p = Path(source)
        name, data = (name_hint or p.name), None
        if not _needs_legacy(name):
            return source, name
        data = p.read_bytes()
        return _normalize_legacy(data, name)
    if _needs_legacy(name):
        return _normalize_legacy(data, name)
    return source, name
```

**改动 `parse_to_files` / `parse_to_zip`**（两处同样模式，以 `parse_to_zip` 为例；`filename` 进 `_prepare`，现代格式路径零变化）：

```python
def parse_to_zip(data, filename=None, *, image_dir_name="images", demote=False,
                 source_type=None, save_dir=None) -> bytes:
    source, name = _prepare(data, filename)
    ...  # md/txt 早退保持原逻辑，filename 全部换成 name（md/txt 不可能是老格式，_prepare 原样放行）

    def _run(src) -> tuple:  # 主/兜底共用的组装打包段（提取避免复制）
        st = _route_source_type(src, source_type)
        result, geo = _extract_with_images(src, st, str(image_dir))
        doc = _assemble(result, False)
        ...  # 原元数据/demote/prefix/打包逻辑，name 用外层变量
        return zip_bytes

    try:
        return _run(source)
    except _FORMAT_EXC_TYPES as exc:
        raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
        source, name = _normalize_legacy(raw, name, exc_prefix=str(exc))
        return _run(source)
```

实现要求：`parse_to_zip` 中「`_route_source_type` 之后的 doc 组装 + zip 打包」段提取为局部 `_run`，主路径与兜底路径共用——**不得复制粘贴两份**。`parse_to_files` 同理（`_run` 返回 doc 并写文件）。

**`api.py` `_chai_parse` 异常放行**（两处 `except Exception` 前各插一段；路径模式 `api.py:376-380` 附近、zip 模式 `:389-394` 附近）：

```python
        except Document2ChunkError:
            raise  # UnsupportedFormat→400 / MissingDependency→503 / 其他→422（注册的 handler）
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
```

`api.py` 顶部已 import `Document2ChunkError`（无需新增 import）。`TimeoutError` 非 Document2ChunkError → 落 500 ✓（符合 spec 错误映射）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_serve_legacy.py tests/test_serve.py -v`
Expected: 新用例全 PASS；**旧 test_serve.py 全 PASS（回归门禁第一关）**

- [ ] **Step 5: 全量回归**

Run: `uv run pytest tests/ -x -q`
Expected: 全绿（快路径零扰动的硬证据）

- [ ] **Step 6: 提交**

```bash
git add src/document2chunk/serve.py src/document2chunk/api.py tests/test_serve_legacy.py
git commit -m "feat(serve): 老格式归一化接线——后缀快路径+指纹兜底+xls 400+错误带文件名"
```

---

### Task 5: Dockerfile 部署 + 真实环境验证

**Files:**
- Modify: `Dockerfile.d2c`
- Modify: `pyproject.toml`（安装命令加 legacy extra）

**Interfaces:**
- Consumes: Task 1-4 全部
- Produces: 可部署镜像（libreoffice + 中文字体 + legacy extras）

- [ ] **Step 1: Dockerfile.d2c 加系统依赖**

`RUN apt-get` 层改为：

```dockerfile
# 系统依赖（PyMuPDF/pdfplumber 需要；LibreOffice 老格式归一化 + 中文字体防豆腐块）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxrender1 libxext6 libgl1 \
    libreoffice-core libreoffice-writer libreoffice-impress \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
```

（`libreoffice-calc` 二期装——xls/xlsx 本期 400。）

pip 安装行加 legacy：

```dockerfile
RUN pip install --no-cache-dir ".[pdf,ocr,docx,api,viz,legacy]"
```

ENV 追加：

```dockerfile
ENV DOCUMENT2CHUNK_DOC_TARGET=docx \
    DOCUMENT2CHUNK_SOFFICE_TIMEOUT=120
```

- [ ] **Step 2: 环境守卫测试（容器内可跑的真实 soffice 用例）**

追加到 `tests/test_legacy_convert.py`：

```python
def test_container_env_complete():
    """容器内 legacy extra 与 soffice 双就绪；裸机无环境跳过。"""
    try:
        import magika  # noqa: F401
        import olefile  # noqa: F401
    except ImportError:
        pytest.skip("legacy extra 未安装")
    if not lc.soffice_available():
        pytest.skip("soffice 未安装")
```

- [ ] **Step 3: 本机构建镜像冒烟（有 Docker 时）**

Run: `docker build -f Dockerfile.d2c -t d2c:legacy-test .`
Expected: 构建成功。进入容器验证：

```bash
docker run --rm d2c:legacy-test sh -c "soffice --version && python -c 'import magika, olefile; print(\"legacy extras ok\")'"
```

Expected: `LibreOffice 7.x` + `legacy extras ok`。（本机无 Docker 则此步挪到 Task 6 在 112 上做，标注清楚。）

- [ ] **Step 4: 提交**

```bash
git add Dockerfile.d2c pyproject.toml tests/test_legacy_convert.py
git commit -m "chore(deploy): Dockerfile 加 LibreOffice+中文字体+legacy extras"
```

---

### Task 6: VML 验证实验 + DOC_TARGET 定版 + 112 端到端验收

**Files:**
- Modify: `src/document2chunk/serve.py`（仅当实验结论为 VML 时改默认值 `docx`→`pdf`，一行）
- Create: `docs/superpowers/specs/2026-09-01-legacy-format-support-vml-finding.md`（实验记录）

**Interfaces:**
- Consumes: Task 1-5 完成态 + 真实 LibreOffice 环境（112 容器）
- Produces: `DOCUMENT2CHUNK_DOC_TARGET` 默认值的实证定版 + 上线验收

- [ ] **Step 1: VML 实验（112 容器内）**

```bash
# ① 本地写探测脚本（避免引号嵌套地狱）
cat > /tmp/vml_probe.py <<'EOF'
import re
import zipfile

xml = zipfile.ZipFile("/tmp/probe_out/feasibility.docx").read("word/document.xml").decode("utf-8")
print("a:blip DrawingML:", len(re.findall(r"<a:blip", xml)))
print("v:imagedata VML:", len(re.findall(r"<v:imagedata", xml)))
print("w:pict VML容器:", len(re.findall(r"<w:pict", xml)))
EOF

# ② 样本+脚本传 112 → 容器内转换 + 探测
scp D:/temp/404_test/feasibility.doc /tmp/vml_probe.py root@128.23.67.112:/tmp/
ssh root@128.23.67.112 'docker cp /tmp/vml_probe.py document2chunk:/tmp/ && docker cp /tmp/feasibility.doc document2chunk:/tmp/ && docker exec document2chunk sh -c "soffice --headless -env:UserInstallation=file:///tmp/probe_profile --convert-to docx --outdir /tmp/probe_out /tmp/feasibility.doc && python /tmp/vml_probe.py"'
```

判据：`a:blip` > 0 → DrawingML，默认 docx 保持；`v:imagedata` > 0 且 `a:blip` == 0 → VML，默认切 pdf。顺带人工抽转出 docx 的 styles.xml 是否保留 Heading 样式（docx 路标题准的前提）。

- [ ] **Step 2: 定版 + 实验记录**

按结论改/不改 `serve.py` 的 `_doc_target_ext` 默认值；写 `vml-finding.md` 记录：命令、计数、标题样式抽查结论（转出 docx 的 Heading 是否保留）、最终默认值及理由。提交：

```bash
git add src/document2chunk/serve.py docs/superpowers/specs/2026-09-01-legacy-format-support-vml-finding.md
git commit -m "chore(legacy): VML 实验定版 DOC_TARGET 默认值（docx|pdf）"
```

- [ ] **Step 3: 112 部署**

```bash
# 遵循 memory d2c-112-ops：部署目录属主 user，root 需 sudo -u user
ssh root@128.23.67.112
cd /home/user/document2chunk
sudo -u user -H git pull            # gitea remote（先本地 push：git push origin main && git push gitea main）
docker compose -f docker-compose.d2c.yml up -d --build   # .env 必须在位（OCR token）
docker exec document2chunk soffice --version
```

- [ ] **Step 4: 端到端验收（本机 curl）**

```bash
# ① 错标件（本次触发案例）
curl -sS -X POST http://128.23.67.112:9301/parse-pdf -F "file=@D:/可行性报告.docx" -o /d/temp/404_test/e2e_mislabeled.zip -w "%{http_code}\n"
# 期望 200；解包抽查 result.md 标题结构 + 图片
# ② 真后缀 .doc
curl -sS -X POST http://128.23.67.112:9301/parse-pdf -F "file=@D:/temp/404_test/feasibility.doc" -o /d/temp/404_test/e2e_doc.zip -w "%{http_code}\n"
# ③ xls 400
curl -sS -X POST http://128.23.67.112:9301/parse-pdf -F "file=@<任一真实.xls>" -w "%{http_code}\n"
# 期望 400 + "即将支持" + 文件名
# ④ 回归：现代 docx 与 8 月 31 日产物 diff 一致
```

- [ ] **Step 5: 收尾——更新 memory 与 spec 状态**

spec 表头状态改「已实施」；memory `doc-vml-image-gap` 补「已修复（本期）」结论 + 实验定版结果；推送双远端（`git push origin main && git push gitea main`）。

**DoD（全部满足才算完）**：① 单测/集成全绿 ② 旧格式产物 diff 零差异 ③ 错标件 112 端到端 200 且 result.md 含图片引用 ④ xls 400 带文件名 ⑤ 容器 `soffice --version` 通过。
