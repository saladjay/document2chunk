"""ocr-extractor 模块（远程 PaddleOCR 服务 → IR，D11）。"""

from document2chunk.extractors.ocr._client import OcrServiceClient
from document2chunk.extractors.ocr._config import OcrConfig
from document2chunk.extractors.ocr._exceptions import OcrServiceError
from document2chunk.extractors.ocr.extractor import OcrExtractor

__all__ = ["OcrExtractor", "OcrServiceClient", "OcrConfig", "OcrServiceError"]
