"""行级 JSON envelope 组装（Q8/Q9 + Q18/Q19 扩展）。"""
from __future__ import annotations

from document2chunk.extractors.excel.models import ExcelParseResult


def row_text(data: dict[str, object]) -> str:
    """`"键: 值; "` 平铺；值为 None 的键值对不进 text。"""
    return "; ".join(f"{k}: {v}" for k, v in data.items() if v is not None)


def to_envelope(result: ExcelParseResult) -> dict:
    return {
        "file": result.file,
        "warnings": result.warnings,
        "rows": [
            {"sheet": r.sheet, "row": r.row, "data": r.data, "text": r.text, "meta": r.meta}
            for r in result.rows
        ],
        "blocks": [
            {"sheet": b.sheet, "kind": b.kind, "text": b.text, "meta": b.meta}
            for b in result.blocks
        ],
        "sheets": result.sheets,
    }
