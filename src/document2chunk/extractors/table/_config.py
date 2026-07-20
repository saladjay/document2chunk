"""table-extractor 配置（env + .env 自动加载，复用 OCR 的 _load_dotenv）。token 禁止入库。

环境变量（与 pandocr-web/OCR token 共用）：
    DOCUMENT2CHUNK_TABLE_TOKEN / DOCUMENT2CHUNK_TABLE_ENDPOINT /
    DOCUMENT2CHUNK_TABLE_TIMEOUT / DOCUMENT2CHUNK_TABLE_FMT
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 复用 OCR 的 .env 加载器（零依赖、cwd/上级 .env、真实环境变量优先）
from document2chunk.extractors.ocr._config import _load_dotenv, _env


@dataclass
class TableConfig:
    endpoint: str = "http://128.23.67.112:8000"
    token: str = ""
    timeout: float = 900.0  # 完整流水线端点上限 900s（含冷启动）
    fmt: str = "html,json"  # html 必给；json 给 cell_box_list + rec_scores
    retry_on_504: int = 1  # 冷启动首请求可能 504，重试次数

    @classmethod
    def from_env(cls) -> "TableConfig":
        _load_dotenv()
        return cls(
            endpoint=_env("DOCUMENT2CHUNK_TABLE_ENDPOINT", cls.endpoint).rstrip("/"),
            token=os.environ.get("DOCUMENT2CHUNK_TABLE_TOKEN", ""),
            timeout=float(_env("DOCUMENT2CHUNK_TABLE_TIMEOUT", str(cls.timeout))),
            fmt=_env("DOCUMENT2CHUNK_TABLE_FMT", cls.fmt),
            retry_on_504=int(_env("DOCUMENT2CHUNK_TABLE_RETRY_504", str(cls.retry_on_504))),
        )

    def require_token(self) -> str:
        if not self.token:
            from document2chunk.extractors.table._exceptions import TableServiceError

            raise TableServiceError(
                "未配置 DOCUMENT2CHUNK_TABLE_TOKEN；请在 .env 或环境变量设置"
            )
        return self.token
