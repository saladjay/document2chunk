"""JSON / JSONL 导出。规范输出 = LogicalDocument JSON（可往返）。"""

from __future__ import annotations

from document2chunk.ir import LogicalDocument


def to_json(doc: LogicalDocument, *, pretty: bool = True) -> str:
    """规范输出（AST JSON，exclude_none）。可被 model_validate_json 往返。"""
    return doc.model_dump_json(exclude_none=True, indent=2 if pretty else None)


def to_jsonl(doc: LogicalDocument) -> str:
    """兼容导出（非规范）：每行一个 content 块的 JSON。"""
    return "\n".join(b.model_dump_json(exclude_none=True) for b in doc.content)
