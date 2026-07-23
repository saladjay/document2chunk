"""Markdown → ``List[Block]``（MinerU HTTP ``/file_parse`` 只返回 md_content）。

从 OCR ``_markdown.parse_markdown`` + ``_mapping._element_to_node`` 移植、简化：
空白行分块 → 按 GFM-ish 方言分类（ATX 标题 / HTML 表 / 图片 / 块公式 / 列表 / 段落）
→ Block。不解析 bbox/provenance（markdown 不携带），不解码图片 base64（仅留路径引用）。

标题 level 来自 ``#`` 数量（= MinerU 的标题判定）；正文 level=None。
列表按行判定（连续 ``- ``/``1. `` 行聚合成一个 list block）。
"""

from __future__ import annotations

import re
from typing import List

from .model import TEXT, TABLE, IMAGE, EQUATION, LIST_T, Block

# 单行匹配（不加 re.S，避免跨行吞并）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
_UL_RE = re.compile(r"^[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\d+[).]\s+(.*)$")
_BLOCKMATH_RE = re.compile(r"^\$\$(.+)\$\$$", re.S)   # 块公式可跨行
_HTML_IMG_RE = re.compile(r'<img\s[^>]*?\bsrc=["\']([^"\']+)["\'][^>]*>', re.I)
_HTML_IMG_ALT_RE = re.compile(r'<img\s[^>]*?\balt=["\']([^"\']*)["\']', re.I)


def parse_markdown(md: str) -> List[Block]:
    """markdown 字符串 → Block 列表（标题/段落/表/图/公式/列表）。"""
    blocks = re.split(r"\n\s*\n", (md or "").strip())
    out: List[Block] = []

    for raw in blocks:
        b = raw.strip()
        if not b:
            continue

        m = _HEADING_RE.match(b)
        if m:
            out.append(Block(type=TEXT, text=m.group(2).strip(), level=len(m.group(1))))
            continue

        if b[:6].lower() == "<table":
            out.append(Block(type=TABLE, table_body=b))
            continue

        m = _BLOCKMATH_RE.match(b)
        if m:
            out.append(Block(type=EQUATION, latex=m.group(1).strip()))
            continue

        # 块公式 \[ ... \]（可跨多行）
        if b[:2] == "\\[":
            latex = b[2:]
            if latex.endswith("\\]"):
                latex = latex[:-2]
            out.append(Block(type=EQUATION, latex=latex.strip()))
            continue

        m = _IMAGE_LINE_RE.match(b)
        if m:
            out.append(Block(type=IMAGE, img_path=m.group(2), caption=m.group(1) or None))
            continue

        # HTML <img>（盖章/页眉渲染成 <div><img .../></div>）
        if "<img" in b.lower():
            m = _HTML_IMG_RE.search(b)
            if m:
                alt_m = _HTML_IMG_ALT_RE.search(b)
                alt = alt_m.group(1).strip() if alt_m and alt_m.group(1).strip() else None
                out.append(Block(type=IMAGE, img_path=m.group(1), caption=alt))
                continue

        # 列表：非空行全部是同一类列表标记 → 聚合
        lines = [ln for ln in b.splitlines() if ln.strip()]
        ul = [_UL_RE.match(ln).group(1).strip() for ln in lines if _UL_RE.match(ln)]
        if ul and len(ul) == len(lines):
            out.append(Block(type=LIST_T, ordered=False, items=ul))
            continue
        ol = [_OL_RE.match(ln).group(1).strip() for ln in lines if _OL_RE.match(ln)]
        if ol and len(ol) == len(lines):
            out.append(Block(type=LIST_T, ordered=True, items=ol))
            continue

        # 段落
        out.append(Block(type=TEXT, text=b))

    return out
