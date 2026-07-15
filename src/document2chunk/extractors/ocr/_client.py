"""OcrServiceClient —— 远程 PaddleOCR 服务的 HTTP 客户端。

- httpx 调 /api/<model>（multipart file）。
- 超时/5xx 指数退避重试（max_retries，澄清2 H21）；4xx 不重试。
- active 模型查询；模型切换全局加锁（澄清2 B4，单 GPU）。
- 服务不可达/超时/未就绪 → OcrServiceError。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import httpx

from document2chunk.extractors.ocr._config import OcrConfig, endpoint_for
from document2chunk.extractors.ocr._exceptions import OcrServiceError


class OcrServiceClient:
    def __init__(self, config: Optional[OcrConfig] = None, *, http_client=None) -> None:
        self._cfg = config or OcrConfig.from_env()
        self._http = http_client  # 可注入（测试用 mock）
        self._switch_lock = threading.Lock()

    # ---------- active 模型 ----------
    def active_model(self) -> str:
        """GET /api/model-runtime → activeModelId（归一到端点别名）。"""
        url = self._cfg.endpoint + "/api/model-runtime"
        headers = self._auth_headers()
        try:
            r = self._request("GET", url, headers)
        except OcrServiceError:
            raise
        data = r.json()
        return data.get("activeModelId") or self._cfg.model

    # ---------- 解析 ----------
    def parse(self, media_bytes: bytes, filename: str, *, model: str) -> dict:
        """POST /api/<model>（multipart file）→ 服务响应 dict。失败抛 OcrServiceError。"""
        path = endpoint_for(model)
        url = self._cfg.endpoint + path
        headers = self._auth_headers()
        files = {"file": (filename, media_bytes)}

        last_err: Optional[str] = None
        for attempt in range(1, self._cfg.max_retries + 1):
            try:
                resp = self._request("POST", url, headers, files=files)
            except httpx.HTTPError as e:
                last_err = f"网络/超时: {e}"
                if attempt < self._cfg.max_retries:
                    time.sleep(self._backoff(attempt))
                    continue
                raise OcrServiceError(f"OCR 请求失败（{self._cfg.max_retries} 次）: {e}", model=model)

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as e:
                    raise OcrServiceError(f"OCR 响应非 JSON: {e}", model=model)

            # 非 200
            if 500 <= resp.status_code < 600 and attempt < self._cfg.max_retries:
                last_err = f"HTTP {resp.status_code}"
                time.sleep(self._backoff(attempt))
                continue
            body = resp.text[:200] if resp is not None else ""
            raise OcrServiceError(
                f"OCR 服务返回 {resp.status_code}: {body}",
                status_code=resp.status_code,
                model=model,
            )

        raise OcrServiceError(
            f"OCR 重试 {self._cfg.max_retries} 次仍失败: {last_err}", model=model
        )

    # ---------- 内部 ----------
    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._cfg.token}"} if self._cfg.token else {}

    def _backoff(self, attempt: int) -> float:
        return 0.5 * (2 ** (attempt - 1))

    def _request(self, method, url, headers, *, files=None):
        """发请求；http_client 可注入。"""
        if self._http is not None:
            # 注入的 mock/client（测试）
            return self._http.request(method, url, headers=headers, files=files)
        with httpx.Client(timeout=self._cfg.timeout) as c:
            return c.request(method, url, headers=headers, files=files)
