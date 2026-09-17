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
