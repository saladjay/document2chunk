"""StyleRegistry —— styles.xml 解析、basedOn 继承链、标题检测、rPr 合并。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lxml import etree

from document2chunk.extractors.docx._ooxml import W, w, wa


@dataclass
class StyleDef:
    style_id: str
    type: str = "paragraph"
    based_on: Optional[str] = None
    name: Optional[str] = None
    rpr_elem: Optional[etree._Element] = None
    is_heading: bool = False
    heading_level: Optional[int] = None


_HEADING_ID_RE = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)
# 中文样式名："标题 1" / "标题1" / " Heading 1 "
_HEADING_NAME_RES = [
    re.compile(r"^heading\s*([1-9])$", re.IGNORECASE),
    re.compile(r"^标题\s*([1-9])$"),
]


def parse_rpr(rpr_elem: Optional[etree._Element]) -> Dict[str, object]:
    """从 <w:rPr> 提取 {font, size, bold, italic}（缺省 None）。"""
    out: Dict[str, object] = {"font": None, "size": None, "bold": None, "italic": None}
    if rpr_elem is None:
        return out

    fonts = rpr_elem.find(w("rFonts"))
    if fonts is not None:
        for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
            val = wa(fonts, attr)
            if val:
                out["font"] = val
                break

    sz = rpr_elem.find(w("sz"))
    if sz is not None:
        val = wa(sz, "val")
        if val:
            try:
                out["size"] = int(val) / 2.0  # half-point → pt
            except ValueError:
                pass

    b = rpr_elem.find(w("b"))
    if b is not None:
        out["bold"] = wa(b, "val") not in ("0", "false")

    i = rpr_elem.find(w("i"))
    if i is not None:
        out["italic"] = wa(i, "val") not in ("0", "false")

    return out


class StyleRegistry:
    """样式注册表 + 继承链解析。"""

    def __init__(self) -> None:
        self._styles: Dict[str, StyleDef] = {}
        self._doc_defaults_rpr: Optional[etree._Element] = None

    # ---------------- 解析 ----------------

    def load(self, styles_elem: Optional[etree._Element]) -> None:
        if styles_elem is None:
            return

        doc_defaults = styles_elem.find(w("docDefaults"))
        if doc_defaults is not None:
            rpr_def = doc_defaults.find(f"{w('rPrDefault')}/{w('rPr')}")
            if rpr_def is not None:
                self._doc_defaults_rpr = rpr_def

        for style_elem in styles_elem.findall(w("style")):
            sid = wa(style_elem, "styleId")
            if not sid:
                continue
            sdef = StyleDef(
                style_id=sid,
                type=wa(style_elem, "type") or "paragraph",
            )
            name_el = style_elem.find(f"{w('name')}")
            if name_el is not None:
                sdef.name = wa(name_el, "val")
            based = style_elem.find(w("basedOn"))
            if based is not None:
                sdef.based_on = wa(based, "val")
            sdef.rpr_elem = style_elem.find(w("rPr"))
            lvl = self._detect_heading_level(sdef)
            if lvl:
                sdef.is_heading = True
                sdef.heading_level = lvl
            self._styles[sid] = sdef

    @staticmethod
    def _detect_heading_level(sdef: StyleDef) -> Optional[int]:
        m = _HEADING_ID_RE.match(sdef.style_id or "")
        if m:
            return int(m.group(1))
        for rx in _HEADING_NAME_RES:
            m = rx.match((sdef.name or "").strip())
            if m:
                return int(m.group(1))
        return None

    # ---------------- 查询 ----------------

    def get(self, style_id: Optional[str]) -> Optional[StyleDef]:
        if not style_id:
            return None
        return self._styles.get(style_id)

    def _chain(self, style_id: Optional[str]) -> List[str]:
        """返回继承链（从根到自身），检测循环。"""
        chain: List[str] = []
        seen = set()
        cur = style_id
        while cur and cur in self._styles and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self._styles[cur].based_on
        chain.reverse()  # root → leaf
        return chain

    def heading_level(self, style_id: Optional[str]) -> Optional[int]:
        """沿 basedOn 链查找标题层级。"""
        for sid in self._chain(style_id):
            sdef = self._styles.get(sid)
            if sdef and sdef.is_heading:
                return sdef.heading_level
        return None

    def merged_rpr(self, style_id: Optional[str]) -> Dict[str, object]:
        """合并 docDefaults → 继承链(root→leaf) 的 rPr。"""
        merged = parse_rpr(self._doc_defaults_rpr)
        for sid in self._chain(style_id):
            sdef = self._styles.get(sid)
            if not sdef:
                continue
            for k, v in parse_rpr(sdef.rpr_elem).items():
                if v is not None:
                    merged[k] = v
        return merged
