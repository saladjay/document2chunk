"""table-extractor：远程表格识别服务 → IR TableNode（designs/008）。

复用 OCR 服务的范式（httpx + token env + .env 自动加载 + 可注入 http_client）。
核心是 HTML ``<table> → TableNode``，**保留 colspan/rowspan**（补全现有映射缺的合并）。
"""

from document2chunk.extractors.table._cell_ocr import ocr_cell_texts
from document2chunk.extractors.table._client import TableServiceClient
from document2chunk.extractors.table._config import TableConfig
from document2chunk.extractors.table._exceptions import TableServiceError
from document2chunk.extractors.table._geo_reconstruct import (
    geo_ocr_to_table_node,
    geo_to_table_node,
    try_geo_ocr_to_table_node,
    try_geo_to_table_node,
)
from document2chunk.extractors.table._html_parser import html_to_table_node
from document2chunk.extractors.table.extractor import TableExtractor

__all__ = [
    "TableExtractor",
    "TableServiceClient",
    "TableConfig",
    "TableServiceError",
    "html_to_table_node",
    "geo_to_table_node",
    "try_geo_to_table_node",
    "geo_ocr_to_table_node",
    "try_geo_ocr_to_table_node",
    "ocr_cell_texts",
]
