"""mineru2doc HTTP API（FastAPI 包装 :func:`convert`）。

- ``POST /parse``（multipart 字段 ``file``；可选表单 ``demote=true``、``zip=true``）：
  上传 PDF → 调 MinerU ``:9030`` → 跑正则补救 + 相对栈式定级 → 返回多级标题 Markdown。
  ``zip=true`` 时把 Markdown + 落盘图片打包成 zip 返回。
- ``GET /health`` → ``{"status":"ok","version":...}``。

配置（环境变量）：``MINERU_BASE_URL``（默认 ``http://host.docker.internal:9030``）、
``MINERU_TIMEOUT``（默认 300）。入口：``uvicorn mineru2doc.server:app``。

注：端点用同步 ``def``，FastAPI 把阻塞的 convert（HTTP 调用 ~20s）丢进线程池，不卡事件循环。
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from . import __version__, convert
from .loader import MinerULoaderError

DEFAULT_BASE_URL = os.environ.get("MINERU_BASE_URL", "http://host.docker.internal:9030")
MINERU_TIMEOUT = float(os.environ.get("MINERU_TIMEOUT", "300"))


def _stem(name: Optional[str]) -> str:
    base = os.path.basename(name or "") or "document.pdf"
    return os.path.splitext(base)[0] or "document"


def create_app() -> FastAPI:
    app = FastAPI(title="mineru2doc", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "mineru_base_url": DEFAULT_BASE_URL}

    @app.post("/parse")
    def parse(
        file: UploadFile = File(...),
        demote: bool = Form(False),
        zip: bool = Form(False),  # noqa: A002  # 表单字段名
    ):
        data: bytes = file.file.read()
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        filename = file.filename or "upload.pdf"

        try:
            if zip:
                md = _parse_to_zip(data, filename, demote)
                return md  # 已是 Response
            md = convert(data, base_url=DEFAULT_BASE_URL, demote=demote)
            return JSONResponse({"markdown": md, "filename": filename})
        except MinerULoaderError as e:
            raise HTTPException(status_code=502, detail=f"MinerU 调用失败：{e}")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    return app


def _parse_to_zip(data: bytes, filename: str, demote: bool) -> Response:
    """convert 落盘图片到临时目录 → 打包 md + images/ 为 zip → Response。"""
    with tempfile.TemporaryDirectory() as tmp:
        md = convert(data, base_url=DEFAULT_BASE_URL, demote=demote, image_dir=tmp)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(_stem(filename) + ".md", md)
            for root, _dirs, files in os.walk(tmp):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, tmp))
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{_stem(filename)}.zip"'},
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
