"""纯文本导出（按阅读顺序，扁平 content）。"""

from __future__ import annotations

from document2chunk.ir import LogicalDocument

from document2chunk.export._helpers import block_text


def to_plain_text(doc: LogicalDocument) -> str:
    lines = [block_text(b) for b in doc.content]
    return "\n".join(t for t in lines if t)
