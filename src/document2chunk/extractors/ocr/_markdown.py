"""markdown → 有序元素列表（方案 A 的"建结构"部分）。

处理实测的 GFM-ish 方言：ATX 标题、HTML <table>、![alt](ref) 图片、
`$$..$$` 块公式、`- `/`1)`/`1. ` 列表、段落。
返回元素 dict 列表（kind + 字段），不含 ID/provenance（由 _mapping 补）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.S)
_IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$", re.S)
_OL_RE = re.compile(r"^\d+[).]\s+(.*)$", re.S)
_BLOCKMATH_RE = re.compile(r"^\$\$(.+)\$\$$", re.S)


def parse_markdown(md: str) -> List[Dict[str, Any]]:
    """空白行分块 → 分类为有序元素。"""
    blocks = re.split(r"\n\s*\n", (md or "").strip())
    elements: List[Dict[str, Any]] = []
    pending: Dict[str, Any] | None = None  # 待提交的列表

    def flush() -> None:
        nonlocal pending
        if pending:
            elements.append({"kind": "list", "ordered": pending["ordered"], "items": pending["items"]})
            pending = None

    for raw in blocks:
        b = raw.strip()
        if not b:
            continue

        m = _HEADING_RE.match(b)
        if m:
            flush()
            elements.append({"kind": "heading", "level": len(m.group(1)), "text": m.group(2).strip()})
            continue

        if b[:6].lower() == "<table":
            flush()
            elements.append({"kind": "table", "html": b})
            continue

        m = _BLOCKMATH_RE.match(b)
        if m:
            flush()
            elements.append({"kind": "formula", "latex": m.group(1).strip()})
            continue

        m = _IMAGE_LINE_RE.match(b)
        if m:
            flush()
            elements.append({"kind": "image", "alt": m.group(1), "ref": m.group(2)})
            continue

        m = _UL_RE.match(b)
        if m:
            if pending and pending["ordered"] is False:
                pending["items"].append(m.group(1).strip())
            else:
                flush()
                pending = {"ordered": False, "items": [m.group(1).strip()]}
            continue

        m = _OL_RE.match(b)
        if m:
            if pending and pending["ordered"] is True:
                pending["items"].append(m.group(1).strip())
            else:
                flush()
                pending = {"ordered": True, "items": [m.group(1).strip()]}
            continue

        flush()
        elements.append({"kind": "paragraph", "text": b})

    flush()
    return elements
