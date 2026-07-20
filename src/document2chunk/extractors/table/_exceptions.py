"""table-extractor 异常（镜像 ocr._exceptions）。"""

from __future__ import annotations

from typing import Optional


class TableServiceError(Exception):
    """远程表格识别服务不可达 / 超时 / 模型未就绪 / 返回异常。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
