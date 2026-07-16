"""OCR 配置（环境变量，澄清2 G19/G20）。token 禁止入库。

便捷配置：``from_env`` 会先从 cwd/上级目录的 ``.env`` 读取（仅填充未设置的
``os.environ``，真实环境变量优先）。``.env`` 已在 ``.gitignore``（勿提交真实 token）；
``.env.example`` 是可提交模板。零依赖（不引 python-dotenv）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from document2chunk.extractors.ocr._exceptions import OcrServiceError


def _find_dotenv(max_up: int = 6) -> Optional[Path]:
    """从 cwd 向上查找最近的 ``.env``（最多 max_up 层）。"""
    p = Path.cwd()
    for _ in range(max_up):
        cand = p / ".env"
        if cand.is_file():
            return cand
        if p.parent == p:
            break
        p = p.parent
    return None


def _load_dotenv() -> None:
    """读取 cwd/上级的 ``.env``（``KEY=VALUE``），仅填充**未设置**的 ``os.environ``。

    真实环境变量优先于 ``.env``。无依赖。
    """
    path = _find_dotenv()
    if path is None:
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class OcrConfig:
    endpoint: str = ""
    token: str = ""
    model: str = "vl"          # vl | pp-ocrv6 | unlimited
    timeout: float = 180.0
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "OcrConfig":
        _load_dotenv()  # 本地 .env 便捷配置（真实环境变量优先）
        return cls(
            endpoint=_env("DOCUMENT2CHUNK_OCR_ENDPOINT", "http://128.23.67.112:8000").rstrip("/"),
            token=os.environ.get("DOCUMENT2CHUNK_OCR_TOKEN", ""),
            model=_env("DOCUMENT2CHUNK_OCR_MODEL", "vl"),
            timeout=float(_env("DOCUMENT2CHUNK_OCR_TIMEOUT", "180")),
            max_retries=int(_env("DOCUMENT2CHUNK_OCR_MAX_RETRIES", "3")),
        )


# 模型别名 → 服务端点路径
MODEL_ENDPOINTS = {
    "vl": "/api/paddleocr-vl-1.6",
    "paddleocr-vl-1.6": "/api/paddleocr-vl-1.6",
    "pp-ocrv6": "/api/pp-ocrv6",
    "ppocrv6": "/api/pp-ocrv6",
    "unlimited": "/api/unlimited-ocr",
    "unlimited-ocr": "/api/unlimited-ocr",
}


def endpoint_for(model: str) -> str:
    key = (model or "").lower()
    if key not in MODEL_ENDPOINTS:
        raise OcrServiceError(f"未知 OCR 模型: {model!r}", model=model)
    return MODEL_ENDPOINTS[key]
