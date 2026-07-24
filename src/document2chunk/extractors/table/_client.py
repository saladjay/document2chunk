"""远程表格识别服务客户端（designs/008 §5）。

对接 ``table-rec-api``（经 pandocr-web 代理），主用 ``POST /api/table-recognition``。
token 经 :class:`TableConfig` 注入；``http_client`` 可注入便于 ``httpx.MockTransport`` 单测。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from document2chunk.extractors.table._config import TableConfig
from document2chunk.extractors.table._exceptions import TableServiceError

_RECOGNIZE_PATH = "/api/table-recognition"


class TableServiceClient:
    """表格识别服务 HTTP 客户端。"""

    def __init__(
        self,
        config: Optional[TableConfig] = None,
        *,
        http_client: Any = None,
    ) -> None:
        self.config = config or TableConfig.from_env()
        if http_client is not None:
            self._http = http_client
            self._owns = False
        else:
            import httpx

            self._http = httpx.Client(timeout=self.config.timeout)
            self._owns = True

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def __enter__(self) -> "TableServiceClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.require_token()}"}

    def _check(self, resp) -> None:
        if resp.status_code >= 400:
            raise TableServiceError(
                f"表格识别服务返回 HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
            )

    def recognize(
        self,
        data: bytes,
        filename: str,
        *,
        fmt: Optional[str] = None,
        page_range: str = "all",
    ) -> dict:
        """``POST /api/table-recognition`` → ``{tables, count, formats}``。

        Args:
            data: PDF/图片二进制。
            filename: 文件名（含扩展名，供服务判类型）。
            fmt: 输出格式逗号多选（``html,xlsx,md,json``）；None 用 config.fmt。
            page_range: 页范围（``all`` / ``0-4``）。
        """
        fmt = fmt or self.config.fmt
        files = {"file": (filename, data, "application/octet-stream")}
        data_form = {"fmt": fmt, "page_range": page_range}
        url = f"{self.config.endpoint}{_RECOGNIZE_PATH}"

        last_err: Optional[Exception] = None
        for attempt in range(self.config.retry_on_504 + 1):
            try:
                resp = self._http.post(url, files=files, data=data_form, headers=self._headers())
            except Exception as e:
                last_err = e
                continue  # 网络错误也重试一次
            if resp.status_code in (504, 502, 503) and attempt < self.config.retry_on_504:
                time.sleep(5)
                continue
            self._check(resp)
            return resp.json()
        raise TableServiceError(f"表格识别请求失败（重试后仍失败）：{last_err}")
