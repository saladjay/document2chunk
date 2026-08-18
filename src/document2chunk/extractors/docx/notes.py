"""尾注/脚注 part → 内容块（阶段 B 设计 §4.5）。

真实使用：尾注为参考文献（单文件最多 61 处）；脚注在 799 样本中零使用，
机制相同顺带实现。内容文末集中、按 id 数值序（还原引用顺序）。
"""

from __future__ import annotations

from typing import List

from lxml import etree

from document2chunk.extractors.docx._ooxml import wa
from document2chunk.ir import ParagraphNode


def parse_notes(reader, kind: str) -> List[ParagraphNode]:
    """kind ∈ {"endnote", "footnote"} → 内容块列表（可能为空）。"""
    part = "word/endnotes.xml" if kind == "endnote" else "word/footnotes.xml"
    root = reader.read_xml(part)
    if root is None:
        return []
    tag = "endnote" if kind == "endnote" else "footnote"
    label = "尾注" if kind == "endnote" else "脚注"
    entries = []
    for note in root:
        if etree.QName(note).localname != tag:
            continue
        if wa(note, "type"):  # separator / continuationSeparator 跳过
            continue
        nid = wa(note, "id") or ""
        text = " ".join("".join(note.itertext()).split())
        if not text:
            continue
        entries.append((nid, text))

    def _num(nid: str) -> int:
        try:
            return int(nid)
        except ValueError:
            return 0

    entries.sort(key=lambda e: _num(e[0]))
    return [
        ParagraphNode(
            id=f"note_{kind}_{nid}",
            text=f"[{label}{nid}] {text}",
            metadata={"note": {"type": kind, "id": nid}},
        )
        for nid, text in entries
    ]
