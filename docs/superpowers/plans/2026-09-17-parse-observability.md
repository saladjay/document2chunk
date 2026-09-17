# /parse-pdf 可观测性（文件名日志 + 分阶段耗时）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/parse-pdf` 每次请求在日志中留下文件名（到达即记）与各解析阶段耗时（stdout 人读行 + JSONL 按天滚动文件双路输出）。

**Architecture:** 新模块 `document2chunk/timing.py` 提供 `StageTimer`（阶段/页级计时 + 汇总双路输出）、`JsonlWriter`（按天滚动、失败降级）、ContextVar 上下文（extractor 零签名侵入）。`api._chai_parse` 到达行 + 创建 timer；`serve.parse_to_zip/parse_to_files` 六阶段打点；OCR extractor 页循环页级打点。

**Tech Stack:** 纯 stdlib（logging/time/json/uuid/contextvars/threading），零新依赖。Python ≥3.10。

**Spec:** `docs/superpowers/specs/2026-09-17-parse-observability-design.md`

## Global Constraints

- 零新第三方依赖；只用 stdlib
- 日志行前缀格式固定：`[req=<8位id>] `；三类行 `ARRIVE` / `ocr_page` / `DONE`
- JSONL 目录：环境变量 `DOCUMENT2CHUNK_LOG_DIR`，默认 `<cwd>/logs`；文件名 `timing-YYYYMMDD.jsonl`（按天滚动，本地时间）
- 写盘失败只 warning 一次并降级仅 stdout，绝不影响解析
- `finish` 幂等（首调生效）——serve 内部与 api 外层都调，双重 finish 安全
- ContextVar 无 timer 时 extractor 全部 no-op（旧调用方零影响）
- CLI 模式（`parse_to_files` 无 timer）自动获得 `mode="cli"` 记录
- 不改 `/parse`、`/parse-json`、uvicorn access log

---

### Task 1: `timing.py` 模块（StageTimer / JsonlWriter / ContextVar）

**Files:**
- Create: `src/document2chunk/timing.py`
- Test: `tests/test_timing.py`

**Interfaces:**
- Consumes: 无（仅 stdlib）
- Produces（后续任务依赖的精确签名）:
  - `StageTimer(*, file: Optional[str], size_bytes: Optional[int], mode: str, demote: bool = False, request_id: Optional[str] = None)`；属性 `request_id/file/size_bytes/mode/demote/source_type/ocr_pages_s`
  - `StageTimer.stage(name: str)` — `@contextmanager`，累计同名阶段耗时（秒，round 3）
  - `StageTimer.ocr_page(page_no: int, elapsed: float, total_pages: int)` — page_no 1-based；打 `ocr_page` 行并 append 到 `ocr_pages_s`
  - `StageTimer.finish(status: str, error_type: Optional[str] = None)` — 幂等；stdout DONE 行 + JSONL 一行
  - `new_request_id() -> str`（uuid4 hex 前 8 位）
  - `log_arrive(*, request_id, file, size_bytes, mode, from_ip)` — stdout ARRIVE 行
  - `set_current_timer(t) -> Token` / `reset_current_timer(token)` / `get_current_timer() -> Optional[StageTimer]`
  - `get_writer() -> JsonlWriter`；`JsonlWriter.write(record: dict)`

- [ ] **Step 0: 一次性环境准备**

```bash
cd D:/github/document2chunk
uv sync -q --extra pdf --extra docx --extra api --extra ocr --extra dev
uv run -q --extra dev pytest tests/test_timing.py -v 2>/dev/null || echo "新测试文件尚不存在，正常"
```

- [ ] **Step 1: 写失败测试 `tests/test_timing.py`**

```python
"""timing 模块测试：StageTimer 阶段/页/finish 汇总 + JsonlWriter 降级 + ContextVar。"""
from __future__ import annotations

import json
import logging

import pytest

from document2chunk import timing
from document2chunk.timing import JsonlWriter, StageTimer


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(d))
    return d


def _read_jsonl(log_dir):
    files = list(log_dir.glob("timing-*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def test_finish_ok_writes_jsonl_and_stdout(log_dir, caplog):
    caplog.set_level(logging.INFO, logger="document2chunk.timing")
    t = StageTimer(file="a.pdf", size_bytes=10, mode="zip", demote=False, request_id="abc12345")
    with t.stage("detect"):
        pass
    t.ocr_page(1, 0.5, 2)
    t.ocr_page(2, 1.5, 2)
    t.source_type = "ocr"
    t.finish("ok")

    rec = _read_jsonl(log_dir)[-1]
    assert rec["request_id"] == "abc12345"
    assert rec["file"] == "a.pdf"
    assert rec["size_bytes"] == 10
    assert rec["mode"] == "zip"
    assert rec["demote"] is False
    assert rec["status"] == "ok"
    assert rec["source_type"] == "ocr"
    assert "detect" in rec["stages"]
    assert all(v >= 0 for v in rec["stages"].values())
    assert rec["ocr_pages_s"] == [0.5, 1.5]
    assert isinstance(rec["total_s"], float)
    assert "ts" in rec
    # stdout DONE 摘要行（含 request_id）
    assert any("DONE" in r.getMessage() and "abc12345" in r.getMessage() for r in caplog.records)


def test_finish_idempotent(log_dir):
    t = StageTimer(file="a.pdf", mode="zip", request_id="abc12345")
    t.finish("ok")
    t.finish("error", error_type="X")
    lines = _read_jsonl(log_dir)
    assert len(lines) == 1
    assert lines[0]["status"] == "ok"


def test_finish_error_records_error_type(log_dir):
    t = StageTimer(file="a.pdf", mode="zip", request_id="abc12345")
    with t.stage("detect"):
        pass
    t.finish("error", error_type="InvalidPdfError")
    rec = _read_jsonl(log_dir)[-1]
    assert rec["status"] == "error"
    assert rec["error_type"] == "InvalidPdfError"
    assert "detect" in rec["stages"]


def test_jsonl_write_failure_degrades(log_dir, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="document2chunk.timing")
    bad = log_dir / "notadir"  # 是文件不是目录 → mkdir 必败
    bad.write_text("x")
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(bad))
    w = JsonlWriter()
    w.write({"x": 1})  # 不应抛异常
    w.write({"x": 2})  # 降级后静默，不再刷 warning
    warns = [r for r in caplog.records if "降级" in r.getMessage()]
    assert len(warns) == 1


def test_log_arrive(caplog):
    caplog.set_level(logging.INFO, logger="document2chunk.timing")
    timing.log_arrive(request_id="abc12345", file="x.pdf", size_bytes=5, mode="zip", from_ip="1.2.3.4")
    assert any("ARRIVE" in r.getMessage() and "x.pdf" in r.getMessage() for r in caplog.records)


def test_current_timer_context():
    assert timing.get_current_timer() is None
    t = StageTimer()
    token = timing.set_current_timer(t)
    try:
        assert timing.get_current_timer() is t
    finally:
        timing.reset_current_timer(token)
    assert timing.get_current_timer() is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run -q --extra dev pytest tests/test_timing.py -v
```

期望：FAIL/ERROR —— `ModuleNotFoundError: No module named 'document2chunk.timing'`

- [ ] **Step 3: 实现 `src/document2chunk/timing.py`**

```python
"""timing —— /parse-pdf 可观测性：阶段计时 + 双路输出（stdout 人读 / JSONL 按天滚动）。

见 docs/superpowers/specs/2026-09-17-parse-observability-design.md。
- StageTimer：单请求计时器；stage() 逐阶段累计、ocr_page() 页耗时、finish() 汇总双路输出。
- JsonlWriter：timing-YYYYMMDD.jsonl 按天滚动；写失败 warning 一次并降级仅 stdout。
- current_timer：ContextVar——extractor 层取当前 timer（无则 no-op），零签名侵入。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("document2chunk.timing")

_current_timer: "ContextVar[Optional[StageTimer]]" = ContextVar("current_timer", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


def get_current_timer() -> Optional["StageTimer"]:
    return _current_timer.get()


def set_current_timer(timer: "StageTimer"):
    return _current_timer.set(timer)


def reset_current_timer(token) -> None:
    _current_timer.reset(token)


class StageTimer:
    """单请求计时器。serve 层 stage() 逐阶段累计；finish() 幂等，双路输出。"""

    def __init__(
        self,
        *,
        file: Optional[str] = None,
        size_bytes: Optional[int] = None,
        mode: str = "cli",
        demote: bool = False,
        request_id: Optional[str] = None,
    ) -> None:
        self.request_id = request_id or new_request_id()
        self.file = file
        self.size_bytes = size_bytes
        self.mode = mode
        self.demote = demote
        self.source_type: Optional[str] = None
        self.ocr_pages_s: List[float] = []
        self._t0 = time.perf_counter()
        self._stages: Dict[str, float] = {}
        self._finished = False

    @contextlib.contextmanager
    def stage(self, name: str):
        """阶段计时上下文：同名阶段累计，异常也记录已耗部分。"""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._stages[name] = round(
                self._stages.get(name, 0.0) + time.perf_counter() - t0, 3
            )

    def ocr_page(self, page_no: int, elapsed: float, total_pages: int) -> None:
        """页级耗时（page_no 1-based）。stdout 一行 + JSONL 数组回填。"""
        self.ocr_pages_s.append(round(elapsed, 3))
        logger.info(
            "[req=%s] ocr_page page=%s/%s elapsed=%.2fs",
            self.request_id, page_no, total_pages, elapsed,
        )

    def finish(self, status: str, error_type: Optional[str] = None) -> None:
        """幂等收口：DONE 人读行 + JSONL 汇总行。"""
        if self._finished:
            return
        self._finished = True
        total = round(time.perf_counter() - self._t0, 3)
        record: Dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "request_id": self.request_id,
            "file": self.file,
            "size_bytes": self.size_bytes,
            "mode": self.mode,
            "source_type": self.source_type,
            "demote": self.demote,
            "status": status,
            "total_s": total,
            "stages": dict(self._stages),
            "ocr_pages_s": self.ocr_pages_s,
        }
        if error_type is not None:
            record["error_type"] = error_type
        stages_str = " ".join(f"{k}={v}s" for k, v in record["stages"].items())
        logger.info(
            "[req=%s] DONE status=%s total=%ss %s",
            self.request_id, status, total, stages_str,
        )
        get_writer().write(record)


def log_arrive(
    *,
    request_id: str,
    file: Optional[str],
    size_bytes: Optional[int],
    mode: str,
    from_ip: Optional[str],
) -> None:
    """到达行（stdout only）。楔死请求由此可对账。"""
    logger.info(
        "[req=%s] ARRIVE file=%r size=%sB mode=%s from=%s",
        request_id, file, size_bytes, mode, from_ip or "-",
    )


class JsonlWriter:
    """timing-YYYYMMDD.jsonl 按天滚动；单例 + 锁；写失败降级仅 stdout。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._disabled = False

    def _path(self) -> Path:
        log_dir = Path(os.environ.get("DOCUMENT2CHUNK_LOG_DIR") or (Path.cwd() / "logs"))
        return log_dir / f"timing-{datetime.now():%Y%m%d}.jsonl"

    def write(self, record: Dict[str, Any]) -> None:
        if self._disabled:
            return
        path = self._path()
        line = json.dumps(record, ensure_ascii=False)
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError as e:
            self._disabled = True
            logger.warning("timing JSONL 写盘失败，降级仅 stdout dir=%s: %s", path.parent, e)


_writer: Optional[JsonlWriter] = None
_writer_lock = threading.Lock()


def get_writer() -> JsonlWriter:
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = JsonlWriter()
        return _writer
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run -q --extra dev pytest tests/test_timing.py -v
```

期望：7 passed

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/timing.py tests/test_timing.py
git commit -m "feat(timing): StageTimer/JsonlWriter 可观测性模块——阶段计时+双路输出"
```

---

### Task 2: `serve.py` 六阶段打点（zip/路径/CLI 三路 + 透传/错误）

**Files:**
- Modify: `src/document2chunk/serve.py`（`parse_to_files` 与 `parse_to_zip`；模块 docstring 提一句 timer）
- Test: `tests/test_serve.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `StageTimer / set_current_timer / reset_current_timer`
- Produces:
  - `serve.parse_to_files(source, output_dir, image_dir, *, demote=False, source_type=None, timer: Optional[StageTimer] = None)`
  - `serve.parse_to_zip(data, filename=None, *, image_dir_name="images", demote=False, source_type=None, save_dir=None, timer: Optional[StageTimer] = None)`
  - 语义：timer=None 时内部自建（mode="cli"）；阶段 `detect/extract/assemble/demote/render/pack`；早退 `finish("passthrough")`；异常 `finish("error", error_type)` 后 re-raise；函数返回前 `finish("ok")`

- [ ] **Step 1: 在 `tests/test_serve.py` 追加失败测试**

文件头 import 区追加：

```python
import json
```

文件末尾追加：

```python
# ---------------------------------------------------------------- timing 打点


@pytest.fixture
def timing_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(d))
    return d


def _last_timing(timing_dir):
    files = list(timing_dir.glob("timing-*.jsonl"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])


def test_zip_mode_records_stages(timing_dir):
    serve.parse_to_zip(DOCX_BYTES, "t.docx")
    rec = _last_timing(timing_dir)
    assert rec["status"] == "ok"
    assert rec["file"] == "t.docx"
    assert rec["size_bytes"] == len(DOCX_BYTES)
    assert rec["mode"] == "zip"
    assert rec["source_type"] == "docx"
    for stage in ("detect", "extract", "assemble", "render", "pack"):
        assert stage in rec["stages"], rec["stages"]
    assert "demote" not in rec["stages"]  # demote=False 不出现


def test_demote_stage_recorded_when_enabled(timing_dir):
    serve.parse_to_zip(DOCX_BYTES, "t.docx", demote=True)
    rec = _last_timing(timing_dir)
    assert rec["demote"] is True
    assert "demote" in rec["stages"]


def test_error_records_partial_stages(timing_dir, monkeypatch):
    from document2chunk.extractors.pdf import PdfExtractor

    def _boom(self, source, **kw):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(PdfExtractor, "extract", _boom)
    with pytest.raises(RuntimeError):
        serve.parse_to_zip(b"%PDF-1.4 broken", "bad.pdf", source_type="pdf")  # 显式类型：跳过 detect 的 pdf_detect，让 RuntimeError 落在 extract 阶段
    rec = _last_timing(timing_dir)
    assert rec["status"] == "error"
    assert rec["error_type"] == "RuntimeError"
    assert len(rec["stages"]) >= 1  # 已完成阶段留痕


def test_md_passthrough_status(timing_dir):
    serve.parse_to_zip(MD_BYTES, "t.md")
    rec = _last_timing(timing_dir)
    assert rec["status"] == "passthrough"


def test_cli_default_timer(timing_dir, tmp_path):
    src = tmp_path / "in.docx"
    src.write_bytes(DOCX_BYTES)
    serve.cli_main(["--input", str(src), "--output", str(tmp_path / "out"), "--images", str(tmp_path / "img")])
    rec = _last_timing(timing_dir)
    assert rec["mode"] == "cli"
    assert rec["status"] == "ok"
    assert rec["file"] == "in.docx"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run -q --extra dev pytest tests/test_serve.py -k timing -v
```

期望：5 个新测试 FAIL —— `TypeError: parse_to_zip() got an unexpected keyword argument` 或 `timing_dir` 下无 jsonl 文件断言失败

- [ ] **Step 3: 改 `src/document2chunk/serve.py`**

3a. 模块顶部 import 区（`from document2chunk.api import ...` 之前）加：

```python
from document2chunk.timing import StageTimer, reset_current_timer, set_current_timer
```

3b. `parse_to_files`（当前签名 `def parse_to_files(source, output_dir, image_dir, *, demote=False, source_type=None)`）整体替换为：

```python
def parse_to_files(
    source: Union[str, Path, bytes],
    output_dir: Union[str, Path],
    image_dir: Union[str, Path],
    *,
    demote: bool = False,
    source_type: Any = None,  # noqa: F821
    timer: Optional[StageTimer] = None,
) -> LogicalDocument:
    """路径模式：解析 source，写 output_dir/result.md + image_dir/ 图片。

    timer 缺省时自建（CLI 路径自动获得可观测性）。
    """
    from document2chunk.api import _route_source_type

    output_dir = Path(output_dir)
    image_dir = Path(image_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    name = _source_name(source)
    timer = timer or StageTimer(file=name, mode="cli", demote=demote)
    token = set_current_timer(timer)

    if _is_md_name(name):
        # .md 早退：解码→标题复原→UTF-8 无 BOM 写 result.md；解码失败回退字节直通
        raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
        text = _decode_text(raw)
        out = _restore_text(text, name).encode("utf-8") if text is not None else raw
        (output_dir / "result.md").write_bytes(out)
        reset_current_timer(token)
        timer.finish("passthrough")
        return _minimal_doc(name)

    if _is_txt_name(name):
        # .txt 转录：解码规范化为 UTF-8 无 BOM + 标题复原，写 result.md
        raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
        (output_dir / "result.md").write_bytes(_txt_to_utf8(raw))
        reset_current_timer(token)
        timer.finish("passthrough")
        return _minimal_doc(name)

    try:
        with timer.stage("detect"):
            st = _route_source_type(source, source_type)
        timer.source_type = st.value
        with timer.stage("extract"):
            result, geo = _extract_with_images(source, st, str(image_dir))
        with timer.stage("assemble"):
            doc = _assemble(result, False)

        if doc.metadata.source_file is None and name:
            doc.metadata.source_file = name
        if doc.metadata.source_type is None:
            doc.metadata.source_type = st

        if demote:
            with timer.stage("demote"):
                _apply_demote(doc)
        _prefix_image_ids(doc, image_dir.name + "/")
        with timer.stage("render"):
            md = _doc_markdown(doc)
        with timer.stage("pack"):
            (output_dir / "result.md").write_text(md, encoding="utf-8")
        timer.finish("ok")
        return doc
    except Exception as exc:  # noqa: BLE001
        timer.finish("error", error_type=type(exc).__name__)
        raise
    finally:
        reset_current_timer(token)
```

3c. `parse_to_zip`（当前签名含 `save_dir`）整体替换为：

```python
def parse_to_zip(
    data: bytes,
    filename: Optional[str] = None,
    *,
    image_dir_name: str = "images",
    demote: bool = False,
    source_type: Any = None,  # noqa: F821
    save_dir: Optional[str] = None,
    timer: Optional[StageTimer] = None,
) -> bytes:
    """zip 模式：解析 data，返回 zip 字节流（根目录 result.md + images/）。

    save_dir 非空时落盘留痕（request.bin + response.zip + 解包产物），失败仅告警。
    timer 缺省时自建。
    """
    from document2chunk.api import _route_source_type

    timer = timer or StageTimer(file=filename, size_bytes=len(data), mode="zip", demote=demote)
    token = set_current_timer(timer)

    if _is_md_name(filename):
        # .md 早退：解码→标题复原→UTF-8 无 BOM 打包；解码失败回退原始字节直通
        text = _decode_text(data)
        out = _restore_text(text, filename).encode("utf-8") if text is not None else data
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("result.md", out)
        zip_bytes = buf.getvalue()
        if save_dir:
            _save_zip_artifacts(save_dir, filename, data, zip_bytes)
        reset_current_timer(token)
        timer.finish("passthrough")
        return zip_bytes

    if _is_txt_name(filename):
        # .txt 转录：不进 extractor/postprocess，转 UTF-8 无 BOM + 标题复原后打包
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("result.md", _txt_to_utf8(data))
        zip_bytes = buf.getvalue()
        if save_dir:
            _save_zip_artifacts(save_dir, filename, data, zip_bytes)
        reset_current_timer(token)
        timer.finish("passthrough")
        return zip_bytes

    tmp = Path(tempfile.mkdtemp(prefix="d2c_zip_"))
    try:
        image_dir = tmp / image_dir_name
        with timer.stage("detect"):
            st = _route_source_type(data, source_type)
        timer.source_type = st.value
        with timer.stage("extract"):
            result, geo = _extract_with_images(data, st, str(image_dir))
        with timer.stage("assemble"):
            doc = _assemble(result, False)
        if filename and doc.metadata.source_file is None:
            doc.metadata.source_file = filename
        if doc.metadata.source_type is None:
            doc.metadata.source_type = st
        if demote:
            with timer.stage("demote"):
                _apply_demote(doc)
        _prefix_image_ids(doc, image_dir_name + "/")
        with timer.stage("render"):
            md = _doc_markdown(doc)

        with timer.stage("pack"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("result.md", md)
                if image_dir.exists():
                    for img in sorted(image_dir.rglob("*")):
                        if img.is_file():
                            arc = img.relative_to(tmp).as_posix()  # images/xxx.png
                            zf.write(img, arc)
            zip_bytes = buf.getvalue()
            if save_dir:
                _save_zip_artifacts(save_dir, filename, data, zip_bytes)
        timer.finish("ok")
        return zip_bytes
    except Exception as exc:  # noqa: BLE001
        timer.finish("error", error_type=type(exc).__name__)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        reset_current_timer(token)
```

3d. 模块 docstring 末尾补一行：

```
可观测性：timer（document2chunk.timing.StageTimer）可选传入；缺省自建（CLI 自动记录）。
```

- [ ] **Step 4: 跑测试确认通过（含旧测试不回归）**

```bash
uv run -q --extra dev pytest tests/test_serve.py tests/test_timing.py -v
```

期望：全部 passed（原 test_serve 既有用例 + 新 5 用例 + test_timing 7 用例）

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/serve.py tests/test_serve.py
git commit -m "feat(serve): 六阶段计时打点——detect/extract/assemble/demote/render/pack，透传与错误也留痕"
```

---

### Task 3: `api.py` 到达行 + timer 创建透传

**Files:**
- Modify: `src/document2chunk/api.py`（`_chai_parse`，当前约 351-396 行；模块顶部 import）
- Test: `tests/test_api_timing.py`（新建）

**Interfaces:**
- Consumes: Task 1 `StageTimer / log_arrive`；Task 2 `serve.parse_to_zip(..., timer=)` / `serve.parse_to_files(..., timer=)`
- Produces: 行为约定——ARRIVE 行在调用 serve 前打出；timer 注入 serve；路径模式文件不存在 → `finish("error", "FileMissing")` + 400；serve 抛异常 → api 侧 `finish` 幂等 no-op + 500

- [ ] **Step 1: 写失败测试 `tests/test_api_timing.py`**

```python
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
        "/parse-pdf", files={"file": ("x.pdf", b"%PDF-fake", "application/pdf")
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run -q --extra dev pytest tests/test_api_timing.py -v
```

期望：4 个测试 FAIL —— timer 未透传（`captured["timer"]` 为 None）、无 ARRIVE 行、无 jsonl 文件

- [ ] **Step 3: 改 `src/document2chunk/api.py`**

3a. 模块顶部（`from document2chunk.ir import ...` 之后）加：

```python
from document2chunk.timing import StageTimer, log_arrive
```

3b. `_chai_parse` 整体替换为：

```python
    async def _chai_parse(request: Request):
        """Chai 对接契约（/parse 与 /parse-pdf 共用，与 mineru2doc :9300/parse 一致）：
        路径模式(file_path/output_dir/image_dir)→写 result.md+images，返回 {status:ok}；
        zip 模式(文件二进制)→返回 zip 流(result.md+images/)。可选 demote。
        可观测性：ARRIVE 行（文件名/大小/来源IP）+ StageTimer 全程计时。
        """
        from document2chunk import serve

        ctype = request.headers.get("content-type", "")
        if not ctype.startswith("multipart/"):
            raise HTTPException(status_code=400, detail="需 multipart/form-data")
        try:
            form = await request.form()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="multipart 解析失败（装 python-multipart）") from exc

        demote = str(form.get("demote", "")).lower() == "true"
        file_path = form.get("file_path")
        from_ip = request.client.host if request.client else None

        if file_path:  # 路径模式
            output_dir = form.get("output_dir")
            image_dir = form.get("image_dir")
            if not output_dir or not image_dir:
                raise HTTPException(status_code=400, detail="路径模式需 file_path + output_dir + image_dir")
            fp = str(file_path)
            try:
                size_bytes = os.path.getsize(fp)
            except OSError:
                size_bytes = None
            timer = StageTimer(
                file=os.path.basename(fp), size_bytes=size_bytes, mode="path", demote=demote
            )
            log_arrive(
                request_id=timer.request_id, file=timer.file,
                size_bytes=size_bytes, mode="path", from_ip=from_ip,
            )
            if not os.path.exists(fp):
                timer.finish("error", error_type="FileMissing")
                raise HTTPException(status_code=400, detail=f"文件不存在: {fp}")
            try:
                serve.parse_to_files(fp, str(output_dir), str(image_dir), demote=demote, timer=timer)
            except Exception as exc:  # noqa: BLE001
                timer.finish("error", error_type=type(exc).__name__)  # 幂等，serve 已 finish 则 no-op
                raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
            return {"status": "ok"}

        # zip 模式
        upload = form.get("file") or form.get("document")
        if upload is None:
            raise HTTPException(status_code=400, detail="zip 模式缺少 file 字段")
        data = await upload.read()
        filename = getattr(upload, "filename", None)
        timer = StageTimer(file=filename, size_bytes=len(data), mode="zip", demote=demote)
        log_arrive(
            request_id=timer.request_id, file=filename,
            size_bytes=len(data), mode="zip", from_ip=from_ip,
        )
        # 输出落盘留痕（线上排查用）：DOCUMENT2CHUNK_SAVE_OUTPUT_DIR 未设置则 None、零行为变化
        save_dir = os.environ.get("DOCUMENT2CHUNK_SAVE_OUTPUT_DIR") or None
        try:
            zip_bytes = serve.parse_to_zip(
                data, filename, demote=demote, save_dir=save_dir, timer=timer
            )
        except Exception as exc:  # noqa: BLE001
            timer.finish("error", error_type=type(exc).__name__)  # 幂等，serve 已 finish 则 no-op
            raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
        from fastapi.responses import Response
        return Response(content=zip_bytes, media_type="application/zip")
```

- [ ] **Step 4: 跑测试确认通过（含旧 api 测试不回归）**

```bash
uv run -q --extra dev pytest tests/test_api_timing.py tests/test_api.py tests/test_serve.py -v
```

期望：全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/api.py tests/test_api_timing.py
git commit -m "feat(api): _chai_parse 到达日志（文件名/大小/IP）+ StageTimer 透传，错误路径收口"
```

---

### Task 4: OCR 页级计时

**Files:**
- Modify: `src/document2chunk/extractors/ocr/extractor.py`（import 区 + 页循环约 84-88 行）
- Test: `tests/test_ocr_timing.py`（新建）

**Interfaces:**
- Consumes: Task 1 `timing.get_current_timer()`；`StageTimer.ocr_page(page_no, elapsed, total_pages)`（page_no 1-based）
- Produces: OCR extract 期间每页一行 `ocr_page` + `ocr_pages_s` 回填；无 timer 时零行为变化

- [ ] **Step 1: 写失败测试 `tests/test_ocr_timing.py`**

```python
"""OCR 页级计时：current_timer 回填 ocr_pages_s + ocr_page 行。"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from document2chunk import timing
from document2chunk.extractors.ocr import extractor as ex_mod
from document2chunk.extractors.ocr.extractor import OcrExtractor


class _FakeClient:
    def parse(self, media, fname, *, model):
        return {"markdown": "# 页", "images": {}}


def _fake_iter_pages(data, name):
    for i in range(2):
        yield i, b"img", f"p{i}.png", 100, 100


@pytest.fixture
def fake_pages(monkeypatch):
    monkeypatch.setattr(ex_mod, "iter_pages", _fake_iter_pages)
    monkeypatch.setattr(ex_mod, "page_count", lambda data: 2)


def test_ocr_pages_recorded(fake_pages, caplog):
    caplog.set_level(logging.INFO, logger="document2chunk.timing")
    t = timing.StageTimer(file="s.pdf", mode="zip", request_id="ocr12345")
    token = timing.set_current_timer(t)
    try:
        opts = SimpleNamespace(extract_images=True, ocr_model="vl")
        OcrExtractor(client=_FakeClient()).extract(b"data", options=opts)
    finally:
        timing.reset_current_timer(token)

    assert len(t.ocr_pages_s) == 2
    assert all(v >= 0 for v in t.ocr_pages_s)
    assert any("ocr_page" in r.getMessage() and "1/2" in r.getMessage() for r in caplog.records)


def test_no_timer_is_noop(fake_pages):
    opts = SimpleNamespace(extract_images=True, ocr_model="vl")
    res = OcrExtractor(client=_FakeClient()).extract(b"data", options=opts)  # 不应抛异常
    assert res is not None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run -q --extra dev pytest tests/test_ocr_timing.py -v
```

期望：`test_ocr_pages_recorded` FAIL（`ocr_pages_s` 为空、无 ocr_page 行）；`test_no_timer_is_noop` 可能已过

- [ ] **Step 3: 改 `src/document2chunk/extractors/ocr/extractor.py`**

3a. import 区（`from pathlib import Path` 之后）加：

```python
import time
```

以及（`from document2chunk.extractors.ocr._chunker import ...` 之前）加：

```python
from document2chunk import timing
```

3b. 页循环（`for page_index, media, fname, pw, ph in iter_pages(...)` 内，`resp = self._client.parse(...)` 一行）替换为：

```python
            _t0 = time.perf_counter()
            resp = self._client.parse(media, fname, model=model)
            _timer = timing.get_current_timer()
            if _timer is not None:
                _timer.ocr_page(page_index + 1, time.perf_counter() - _t0, pcount)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run -q --extra dev pytest tests/test_ocr_timing.py -v
```

期望：2 passed

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/ocr/extractor.py tests/test_ocr_timing.py
git commit -m "feat(ocr): 页级耗时打点——ocr_page 行 + timer 回填，无 timer 零影响"
```

---

### Task 5: docker-compose 挂卷 + 全量回归

**Files:**
- Modify: `docker-compose.d2c.yml`（volumes 列表）

**Interfaces:**
- Consumes: 前四个任务的完整实现
- Produces: 部署配置就绪（`/app/logs` 挂到宿主 `./d2c_logs`）

- [ ] **Step 1: 改 `docker-compose.d2c.yml` volumes**

```yaml
    volumes:
      - ./d2c_outputs:/app/outputs   # 留痕目录挂载（留痕关闭时空置；已存文件可继续查看）
      - ./d2c_logs:/app/logs         # timing JSONL（timing-YYYYMMDD.jsonl 按天滚动）
```

- [ ] **Step 2: 全量回归**

```bash
uv run -q --extra dev pytest -q
```

期望：全部 passed，0 failed（既有全量 + 新增 18 个用例）

- [ ] **Step 3: 本地冒烟（真实链路，md 直通零依赖）**

```bash
uv run -q --extra dev python -c "
from document2chunk import serve
serve.parse_to_zip('# 冒烟'.encode('utf-8'), '冒烟.md')
" && ls logs/ && head -c 400 logs/timing-*.jsonl && echo && rm -rf logs
```

期望：`logs/timing-YYYYMMDD.jsonl` 存在，JSON 行含 `"file":"冒烟.md"` 与 `"status":"passthrough"`

- [ ] **Step 4: Commit**

```bash
git add docker-compose.d2c.yml
git commit -m "chore(docker): d2c_logs 挂卷——timing JSONL 持久化"
```

---

## 部署提示（实现完成后）

112 上重新部署（走 DEPLOY_112 既有流程 + `git pull`）：`cd /home/user/document2chunk && sudo -u user -H git pull ... && docker compose -f docker-compose.d2c.yml up -d --build`；验证：发一个真实 PDF 后 `docker logs document2chunk | grep ARRIVE` 与 `ls d2c_logs/`。
