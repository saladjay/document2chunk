"""OCR 配置（环境变量，澄清2 G19/G20）。token 禁止入库。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from document2chunk.extractors.ocr._exceptions import OcrServiceError


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
