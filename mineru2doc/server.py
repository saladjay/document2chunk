"""mineru2doc HTTP API（对接 Chai「接口解析」契约）。

契约（接口解析-HTTP解析接口对接需求文档.md）：
- ``POST /parse``（multipart 字段 ``file``，默认名）→ **同步返回一个 zip 字节流**
  （``application/zip``），zip 根目录必须含 ``result.md``（UTF-8），可选 ``images/``。
  后端解压后读 ``result.md`` 作解析结果。
- ``GET /health`` → ``{"status":"ok",...}``。
- 失败：非 2xx + 可读错误（JSON）。

配置（环境变量）：``MINERU_BASE_URL``（默认 ``http://host.docker.internal:9030``）、
``MINERU_TIMEOUT``（默认 300）。入口：``uvicorn mineru2doc.server:app``。

注：端点用同步 ``def``，FastAPI 把阻塞的 convert（HTTP 调用 ~20s）丢进线程池，不卡事件循环。
zip-slip：图片条目均为相对路径 ``images/<name>``，无 ``../``。
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import __version__, convert
from .loader import MinerULoaderError

DEFAULT_BASE_URL = os.environ.get("MINERU_BASE_URL", "http://host.docker.internal:9030")
MINERU_TIMEOUT = float(os.environ.get("MINERU_TIMEOUT", "300"))


def create_app() -> FastAPI:
    app = FastAPI(title="mineru2doc", version=__version__)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "mineru_base_url": DEFAULT_BASE_URL}

    @app.post("/parse")
    def parse(file: UploadFile = File(...), demote: bool = Form(False)):
        """接收文件 → MinerU → 多级标题 Markdown → 返回 zip（根目录 result.md + images/）。"""
        data: bytes = file.file.read()
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        try:
            return _parse_to_zip(data, demote)
        except MinerULoaderError as e:
            raise HTTPException(status_code=502, detail=f"MinerU 调用失败：{e}")
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e

    return app


def _parse_to_zip(data: bytes, demote: bool) -> Response:
    """convert 落盘图片到临时目录 → 打包 result.md + images/ 为 zip → Response。

    - result.md 固定在 zip【根目录】，UTF-8（writestr 默认 UTF-8、无 BOM）。
    - 图片走 ``images/<name>`` 相对路径，与 result.md 内的 ``![](images/...)`` 引用对齐。
    """
    with tempfile.TemporaryDirectory() as tmp:
        md = convert(data, base_url=DEFAULT_BASE_URL, demote=demote, image_dir=tmp)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("result.md", md)
            for root, _dirs, files in os.walk(tmp):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, tmp))
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
