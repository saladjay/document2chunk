"""mineru2doc HTTP API（对接 Chai「接口解析」契约）。

契约（接口解析-HTTP解析接口对接需求文档.md）：
- ``POST /parse``（multipart 字段 ``file``）→ **同步返回 zip 字节流**（``application/zip``），
  zip 根目录含 ``result.md``（UTF-8）+ 可选 ``images/``。后端解压读 ``result.md``。
- ``GET /health`` → ``{"status":"ok",...}``。
- 失败：非 2xx + 可读错误（JSON）。

每次 /parse 记一行结构化日志（成功/失败），便于排查是否接通与解析效果：
``INFO mineru2doc: parse ok file=… bytes=… ms=… headings=… levels={…} images=…``。

配置（环境变量）：``MINERU_BASE_URL``、``MINERU_TIMEOUT``。入口：``uvicorn mineru2doc.server:app``。
端点用同步 ``def``，阻塞的 convert 在线程池跑，不卡事件循环。zip 条目均为相对路径（无 zip-slip）。
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import time
import zipfile
from typing import List, Optional, Tuple

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import __version__, convert
from .loader import MinerULoaderError

DEFAULT_BASE_URL = os.environ.get("MINERU_BASE_URL", "http://host.docker.internal:9030")
MINERU_TIMEOUT = float(os.environ.get("MINERU_TIMEOUT", "300"))

# 独立 logger（propagate=False，不被 uvicorn 重复打），输出到 stderr → docker logs 可见
log = logging.getLogger("mineru2doc")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
log.setLevel(logging.INFO)
log.propagate = False


def _heading_stats(md: str) -> Tuple[int, dict]:
    hs = [l for l in md.splitlines() if re.match(r"^#{1,6}\s", l)]
    levels: dict = {}
    for l in hs:
        n = l.count("#")
        levels[n] = levels.get(n, 0) + 1
    return len(hs), levels


def create_app() -> FastAPI:
    app = FastAPI(title="mineru2doc", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "mineru_base_url": DEFAULT_BASE_URL}

    @app.post("/parse")
    def parse(file: UploadFile = File(...), demote: bool = Form(False)):
        """接收文件 → MinerU → 多级标题 Markdown → zip（根目录 result.md + images/）。"""
        fname = file.filename or "upload.pdf"
        data: bytes = file.file.read()
        if not data:
            log.warning("parse reject file=%s bytes=0 reason=empty", fname)
            raise HTTPException(status_code=400, detail="空文件")

        t0 = time.perf_counter()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                md = convert(data, base_url=DEFAULT_BASE_URL, demote=demote, image_dir=tmp)
                ms = int((time.perf_counter() - t0) * 1000)
                n_head, levels = _heading_stats(md)
                n_img = sum(len(fs) for _, _, fs in os.walk(tmp))
                resp = _zip_response(md, tmp)
            log.info(
                "parse ok file=%s bytes=%d ms=%d headings=%d levels=%s images=%d",
                fname, len(data), ms, n_head, levels, n_img,
            )
            return resp
        except MinerULoaderError as e:
            ms = int((time.perf_counter() - t0) * 1000)
            log.warning("parse fail file=%s bytes=%d ms=%d kind=MinerULoaderError error=%s",
                        fname, len(data), ms, e)
            raise HTTPException(status_code=502, detail=f"MinerU 调用失败：{e}")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            ms = int((time.perf_counter() - t0) * 1000)
            log.warning("parse fail file=%s bytes=%d ms=%d kind=%s error=%s",
                        fname, len(data), ms, type(e).__name__, e)
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    return app


def _zip_response(md: str, image_dir: str) -> Response:
    """打包 result.md（根目录，UTF-8 无 BOM）+ images/<name> 为 zip Response。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("result.md", md)
        for root, _dirs, files in os.walk(image_dir):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, image_dir))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="result.zip"'},
    )


app = create_app()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(prog="python -m mineru2doc.server", description="mineru2doc HTTP API")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    uvicorn.run("mineru2doc.server:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
