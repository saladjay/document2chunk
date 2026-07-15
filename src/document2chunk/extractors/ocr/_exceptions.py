"""OCR 异常（本地定义；待共享 exceptions.py 合并到 main 后对齐 Document2ChunkError）。"""

from __future__ import annotations

from typing import Optional


class OcrServiceError(Exception):
    """远程 PaddleOCR 服务不可达 / 超时 / 模型未就绪。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        model: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.model = model

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.model:
            parts.append(f"model={self.model}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " | ".join(parts)
