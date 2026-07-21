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
# HTML <img src="..."> —— OCR 服务把盖章/页眉渲染成 <div style=...><img .../></div>
# 不转 image 元素会原样落入 paragraph 文本，输出 HTML（R5/R7）。src 即 images 字典 key。
_HTML_IMG_RE = re.compile(r'<img\s[^>]*?\bsrc=["\']([^"\']+)["\'][^>]*>', re.I)
_HTML_IMG_ALT_RE = re.compile(r'<img\s[^>]*?\balt=["\']([^"\']*)["\']', re.I)


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

        # 块公式 \[ ... \]（服务实测输出格式，可跨多行）
        if b[:2] == "\\[":
            latex = b[2:]
            if latex.endswith("\\]"):
                latex = latex[:-2]
            flush()
            elements.append({"kind": "formula", "latex": latex.strip()})
            continue

        m = _IMAGE_LINE_RE.match(b)
        if m:
            flush()
            elements.append({"kind": "image", "alt": m.group(1), "ref": m.group(2)})
            continue

        # HTML <img>（盖章/页眉：服务渲染成 <div><img src="imgs/..."/></div>）
        if "<img" in b.lower():
            m = _HTML_IMG_RE.search(b)
            if m:
                flush()
                alt_m = _HTML_IMG_ALT_RE.search(b)
                alt = (alt_m.group(1).strip() if alt_m and alt_m.group(1).strip() else "Image")
                elements.append({"kind": "image", "alt": alt, "ref": m.group(1)})
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
