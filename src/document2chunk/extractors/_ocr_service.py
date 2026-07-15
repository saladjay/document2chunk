"""远程 PaddleOCR 服务客户端（D11）。

对接「PaddleOCR 三件套」服务（PP-OCRv6 / PaddleOCR-VL / Unlimited-OCR），
服务文档见 ``D:\\project\\server\\PaddleOCR三件套使用文档.md``。

- token / endpoint / 超时 由 :class:`OcrConfig`（环境变量）注入，**禁止硬编码 token**。
- 所有请求带 ``Authorization: Bearer <token>``。
- 模型切换 + 就绪轮询；不可达 / 超时 / 未就绪 → :class:`OcrServiceError`。

依赖 httpx（pyproject ``ocr`` extra）。``http_client`` 可注入便于用
``httpx.MockTransport`` 单测。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from document2chunk.exceptions import OcrServiceError

# 模型 ID → 解析端点
MODEL_ENDPOINTS: dict[str, str] = {
    "pp-ocrv6": "/api/pp-ocrv6",
    "paddleocr-vl-1.6": "/api/paddleocr-vl-1.6",
    "unlimited-ocr": "/api/unlimited-ocr",
}

VALID_MODELS = frozenset(MODEL_ENDPOINTS)


@dataclass
class OcrConfig:
    """OCR 服务配置（token 必须由环境变量注入，禁止硬编码）。"""

    token: str = ""
    endpoint: str = "http://128.23.67.112:8000"
    timeout: float = 300.0
    default_model: str = "paddleocr-vl-1.6"
    switch_timeout: float = 1200.0  # 模型切换/加载最长等待
    poll_interval: float = 5.0

    @classmethod
    def from_env(cls) -> "OcrConfig":
        return cls(
            token=os.environ.get("PANDOCR_TOKEN", ""),
            endpoint=os.environ.get("PANDOCR_ENDPOINT", cls.endpoint),
            timeout=float(os.environ.get("PANDOCR_TIMEOUT", cls.timeout)),
            default_model=os.environ.get("PANDOCR_DEFAULT_MODEL", cls.default_model),
        )

    def require_token(self) -> str:
        if not self.token:
            raise OcrServiceError(
                "未配置 PANDOCR_TOKEN（远程 OCR 服务 token）；"
                "请设置环境变量 PANDOCR_TOKEN"
            )
        return self.token


def _model_ready(runtime: dict, model_id: str) -> bool:
    """运行态 JSON 中目标模型是否就绪（best-effort：activeModelId 命中即视为就绪）。

    服务 runtime 结构（activeModelId + 各模型 state）随部署版本略有差异，
    命中 activeModelId 是最稳的就绪信号；命中后再由 parse 端点的 200 兜底。
    """
    if not isinstance(runtime, dict):
        return False
    if runtime.get("activeModelId") != model_id:
        return False
    # 若提供 operation/state 或 models[id].state，额外校验非 starting
    op = runtime.get("operation")
    if isinstance(op, dict) and op.get("state") == "starting":
        return False
    models = runtime.get("models")
    if isinstance(models, dict):
        st = models.get(model_id)
        if isinstance(st, dict) and st.get("state") not in (None, "ready"):
            return False
    return True


class OcrServiceClient:
    """远程 PaddleOCR 服务 HTTP 客户端。"""

    def __init__(
        self,
        config: Optional[OcrConfig] = None,
        *,
        http_client: Any = None,
    ):
        self.config = config or OcrConfig.from_env()
        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            import httpx

            self._http = httpx.Client(timeout=self.config.timeout)
            self._owns_http = True

    # ---- 资源 ----

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "OcrServiceClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- 内部 ----

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.require_token()}"}

    def _check(self, resp) -> None:
        if resp.status_code >= 400:
            raise OcrServiceError(
                f"OCR 服务返回 HTTP {resp.status_code}: {resp.text[:200]}"
            )

    # ---- API ----

    def runtime(self) -> dict:
        """GET /api/model-runtime → 运行态（activeModelId / 各模型 state）。"""
        try:
            resp = self._http.get(
                f"{self.config.endpoint}/api/model-runtime",
                headers=self._headers(),
            )
        except Exception as e:
            raise OcrServiceError(f"OCR 服务不可达：{e}") from e
        self._check(resp)
        return resp.json()

    def switch_model(self, model_id: str) -> dict:
        if model_id not in VALID_MODELS:
            raise OcrServiceError(f"未知 OCR 模型：{model_id}")
        resp = self._http.post(
            f"{self.config.endpoint}/api/model-runtime/switch",
            json={"modelId": model_id},
            headers=self._headers(),
        )
        self._check(resp)
        return resp.json()

    def ensure_model(self, model_id: str) -> None:
        """确保目标模型就绪：已就绪直接返回；否则切换并轮询。"""
        if _model_ready(self.runtime(), model_id):
            return
        self.switch_model(model_id)
        deadline = time.monotonic() + self.config.switch_timeout
        while time.monotonic() < deadline:
            time.sleep(self.config.poll_interval)
            if _model_ready(self.runtime(), model_id):
                return
        raise OcrServiceError(
            f"OCR 模型 {model_id} 在 {self.config.switch_timeout:.0f}s 内未就绪"
        )

    def parse(
        self,
        data: bytes,
        filename: str,
        *,
        model: str,
    ) -> dict:
        """POST /api/{model} 解析文件 → {markdown, images, layoutParsingResults, ...}。"""
        path = MODEL_ENDPOINTS.get(model)
        if path is None:
            raise OcrServiceError(f"未知 OCR 模型：{model}")
        files = {"file": (filename, data, "application/octet-stream")}
        try:
            resp = self._http.post(
                f"{self.config.endpoint}{path}",
                files=files,
                headers=self._headers(),
            )
        except Exception as e:
            raise OcrServiceError(f"OCR 解析请求失败：{e}") from e
        self._check(resp)
        return resp.json()
