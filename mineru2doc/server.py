"""mineru2doc HTTP API（对接 Chai「MinerU-Adapter」契约 v2.0）。

``POST /parse`` 按请求字段自动判模式：

- **路径模式**（``output-path=true``，共享文件系统）：表单含 ``file_path``（+ ``output_dir``
  + ``image_dir``）→ 服务读该文件，把 ``result.md`` 写入 ``output_dir``、图片写入 ``image_dir``，
  返回 JSON ``{"status":"ok"}``；失败返回非 2xx + ``{"status":"error","message":...}``。
- **zip 模式**（``output-path=false``，跨机器）：表单含 ``file``（binary）→ 返回 zip 字节流，
  根目录 ``result.md`` + ``images/``。

``GET /health`` → ``{"status":"ok",...}``。每次解析记一行结构化日志。配置：``MINERU_BASE_URL``、
``MINERU_TIMEOUT``。端点同步 ``def``（线程池跑阻塞 convert）。
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
from fastapi.responses import JSONResponse, Response

from . import __version__, convert, convert_to_dir
from .loader import MinerULoaderError

DEFAULT_BASE_URL = os.environ.get("MINERU_BASE_URL", "http://host.docker.internal:9030")
MINERU_TIMEOUT = float(os.environ.get("MINERU_TIMEOUT", "300"))

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
    def parse(
        file: Optional[UploadFile] = File(None),
        file_path: Optional[str] = Form(None),
        output_dir: Optional[str] = Form(None),
        image_dir: Optional[str] = Form(None),
        demote: bool = Form(False),
    ):
        # 路径模式：有 file_path 字段
        if file_path:
            return _path_mode(file_path, output_dir, image_dir, demote)
        # zip 模式：有 file 上传
        if file is not None:
            return _zip_mode(file, demote)
        raise HTTPException(status_code=400, detail="缺少 file（zip模式）或 file_path（路径模式）")

    return app


# ── 路径模式 ──

def _path_mode(file_path: str, output_dir: Optional[str], image_dir: Optional[str], demote: bool):
    if not output_dir:
        log.warning("parse-path reject file=%s reason=no_output_dir", file_path)
        return JSONResponse(status_code=400, content={"status": "error", "message": "路径模式需 output_dir"})
    if not os.path.exists(file_path):
        log.warning("parse-path reject file=%s reason=not_found", file_path)
        return JSONResponse(status_code=400, content={"status": "error", "message": f"文件不存在: {file_path}"})

    t0 = time.perf_counter()
    try:
        result_md = convert_to_dir(file_path, output_dir, image_dir, base_url=DEFAULT_BASE_URL, demote=demote)
        ms = int((time.perf_counter() - t0) * 1000)
        md = open(result_md, encoding="utf-8").read()
        n_head, levels = _heading_stats(md)
        n_img = len(os.listdir(image_dir)) if image_dir and os.path.isdir(image_dir) else 0
        log.info("parse-path ok file=%s output=%s ms=%d headings=%d levels=%s images=%d",
                 file_path, output_dir, ms, n_head, levels, n_img)
        return JSONResponse(content={"status": "ok"})
    except MinerULoaderError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        log.warning("parse-path fail file=%s ms=%d kind=MinerULoaderError error=%s", file_path, ms, e)
        return JSONResponse(status_code=502, content={"status": "error", "message": f"MinerU 调用失败：{e}"})
    except Exception as e:  # noqa: BLE001
        ms = int((time.perf_counter() - t0) * 1000)
        log.warning("parse-path fail file=%s ms=%d kind=%s error=%s", file_path, ms, type(e).__name__, e)
        return JSONResponse(status_code=500, content={"status": "error", "message": f"{type(e).__name__}: {e}"})


# ── zip 模式 ──

def _zip_mode(file: UploadFile, demote: bool):
    fname = file.filename or "upload.pdf"
    data: bytes = file.file.read()
    if not data:
        log.warning("parse-zip reject file=%s bytes=0 reason=empty", fname)
        raise HTTPException(status_code=400, detail="空文件")

    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            md = convert(data, base_url=DEFAULT_BASE_URL, demote=demote, image_dir=tmp)
            ms = int((time.perf_counter() - t0) * 1000)
            n_head, levels = _heading_stats(md)
            n_img = sum(len(fs) for _, _, fs in os.walk(tmp))
            resp = _zip_response(md, tmp)
        log.info("parse-zip ok file=%s bytes=%d ms=%d headings=%d levels=%s images=%d",
                 fname, len(data), ms, n_head, levels, n_img)
        return resp
    except MinerULoaderError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        log.warning("parse-zip fail file=%s bytes=%d ms=%d kind=MinerULoaderError error=%s",
                    fname, len(data), ms, e)
        raise HTTPException(status_code=502, detail=f"MinerU 调用失败：{e}")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        ms = int((time.perf_counter() - t0) * 1000)
        log.warning("parse-zip fail file=%s bytes=%d ms=%d kind=%s error=%s",
                    fname, len(data), ms, type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


def _zip_response(md: str, image_dir: str) -> Response:
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
