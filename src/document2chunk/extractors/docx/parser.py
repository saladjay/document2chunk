"""DocumentParser —— document.xml → BlockNode 列表 + TOC 条目。"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from lxml import etree

from document2chunk.extractors.docx._ooxml import A, WP, w, ra, wa
from document2chunk.extractors.docx.styles import StyleRegistry, parse_rpr
from document2chunk.ir import (
    BlockNode,
    HeadingNode,
    HyperlinkNode,
    ImageNode,
    InlineNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    RunNode,
    RunProperties,
    TableCellNode,
    TableNode,
    TableRowNode,
    TocEntry,
)

_HEADING_HEURISTIC = [
    (re.compile(r"^第[一二三四五六七八九十百千]+[章篇部]"), 1),
    (re.compile(r"^\d+(\.\d+)*\s*\S"), 2),
]


class _Numbering:
    """numId+ilvl → 是否有序。"""

    def __init__(self, numbering_elem) -> None:
        self._num_to_abstract = {}
        self._fmt = {}
        if numbering_elem is not None:
            for an in numbering_elem.iter(w("abstractNum")):
                aid = wa(an, "abstractNumId")
                for lvl in an.findall(w("lvl")):
                    ilvl = wa(lvl, "ilvl") or "0"
                    fmt_el = lvl.find(w("numFmt"))
                    fmt = wa(fmt_el, "val") if fmt_el is not None else None
                    if aid is not None:
                        self._fmt[(aid, ilvl)] = fmt
            for num in numbering_elem.iter(w("num")):
                nid = wa(num, "numId")
                an = num.find(w("abstractNumId"))
                if nid is not None and an is not None:
                    self._num_to_abstract[nid] = wa(an, "val")

    def is_ordered(self, num_id: Optional[str], ilvl: str) -> bool:
        aid = self._num_to_abstract.get(num_id)
        fmt = self._fmt.get((aid, ilvl))
        return True if fmt is None else (fmt != "bullet")


class DocumentParser:
    def __init__(
        self,
        registry: StyleRegistry,
        numbering_elem=None,
        reader=None,
        heuristic_headings: bool = False,
    ) -> None:
        self._styles = registry
        self._numbering = _Numbering(numbering_elem)
        self._reader = reader
        self._heuristic = heuristic_headings
        self._bc = 0
        self._rc = 0
        self._toc_entries: List[TocEntry] = []

    def _bid(self) -> str:
        self._bc += 1
        return f"block_{self._bc:06d}"

    def _rid(self) -> str:
        self._rc += 1
        return f"run_{self._rc:06d}"

    # ============ 入口 ============
    def parse(self, document_elem) -> Tuple[List[BlockNode], List[TocEntry]]:
        body = document_elem.find(w("body")) if document_elem is not None else None
        if body is None:
            return [], []
        blocks: List[BlockNode] = []
        list_buf: List[Tuple[str, str, ParagraphNode]] = []
        in_toc = False

        def flush_list() -> None:
            if not list_buf:
                return
            num_id = list_buf[0][0]
            ordered = self._numbering.is_ordered(num_id, list_buf[0][1])
            items = [
                ListItemNode(
                    id=self._bid(),
                    level=self._ilvl_int(ilvl),
                    blocks=[para],
                )
                for _, ilvl, para in list_buf
            ]
            blocks.append(ListNode(id=self._bid(), ordered=ordered, items=items))
            list_buf.clear()

        for child in body:
            tag = etree.QName(child).localname

            if tag == "tbl":
                flush_list()
                blocks.append(self._parse_table(child))
                continue
            if tag != "p":
                continue

            # TOC 域（best-effort）
            if self._has_field_instr(child, "TOC"):
                in_toc = True
                continue
            if in_toc:
                if self._has_field_end(child):
                    in_toc = False
                else:
                    self._collect_toc_entry(child)
                continue

            kind, level, runs, text, list_info, images = self._classify(child)

            # 图片：先冲刷列表，独立成块
            if images:
                flush_list()
                blocks.extend(images)

            if kind == "heading":
                flush_list()
                blocks.append(
                    HeadingNode(id=self._bid(), level=level, text=text, runs=runs)
                )
            elif kind == "list":
                num_id, ilvl = list_info
                # numId 变化 → 先冲刷
                if list_buf and list_buf[-1][0] != num_id:
                    flush_list()
                para = ParagraphNode(id=self._bid(), runs=runs, text=text)
                list_buf.append((num_id, ilvl, para))
            else:
                flush_list()
                if text or runs:
                    blocks.append(ParagraphNode(id=self._bid(), runs=runs, text=text))

        flush_list()
        return blocks, self._toc_entries

    @staticmethod
    def _ilvl_int(ilvl: str) -> int:
        try:
            return int(float(ilvl))
        except (ValueError, TypeError):
            return 0

    # ============ 段落分类 ============
    def _classify(self, p):
        ppr = p.find(w("pPr"))
        pstyle_id = None
        if ppr is not None:
            ps = ppr.find(w("pStyle"))
            if ps is not None:
                pstyle_id = wa(ps, "val")
        runs, text = self._parse_runs(p, pstyle_id)
        level = self._detect_heading(p, pstyle_id, text)
        list_info = self._list_info(ppr)
        images = self._extract_images(p)

        if level and text:
            kind = "heading"
        elif list_info is not None and (text or runs):
            kind = "list"
        else:
            kind = "para"
        return kind, level, runs, text, list_info, images

    def _detect_heading(self, p, pstyle_id, text) -> Optional[int]:
        ppr = p.find(w("pPr"))
        if ppr is not None:
            ol = ppr.find(w("outlineLvl"))
            if ol is not None:
                val = wa(ol, "val")
                if val and val.isdigit():
                    n = int(val)
                    if 0 <= n <= 8:
                        return n + 1
            lvl = self._styles.heading_level(pstyle_id)
            if lvl:
                return lvl
        if self._heuristic and text:
            for rx, lvl in _HEADING_HEURISTIC:
                if rx.match(text.strip()):
                    return lvl
        return None

    def _list_info(self, ppr) -> Optional[Tuple[str, str]]:
        if ppr is None:
            return None
        numpr = ppr.find(w("numPr"))
        if numpr is None:
            return None
        nid_el = numpr.find(w("numId"))
        if nid_el is None:
            return None
        ilvl_el = numpr.find(w("ilvl"))
        num_id = wa(nid_el, "val") or ""
        ilvl = (wa(ilvl_el, "val") if ilvl_el is not None else None) or "0"
        return (num_id, ilvl)

    # ============ runs ============
    def _parse_runs(self, p, pstyle_id) -> Tuple[List[InlineNode], str]:
        inlines: List[InlineNode] = []
        text_parts: List[str] = []
        base = self._styles.merged_rpr(pstyle_id)

        for child in p:
            tag = etree.QName(child).localname
            if tag == "r":
                t, run = self._parse_run(child, base)
                if run is not None:
                    inlines.append(run)
                    text_parts.append(t)
            elif tag == "hyperlink":
                hl_runs: List[RunNode] = []
                hl_text: List[str] = []
                for r in child.findall(w("r")):
                    t, run = self._parse_run(r, base)
                    if run is not None:
                        hl_runs.append(run)
                        hl_text.append(t)
                target = ra(child, "id") or child.get(w("anchor")) or ""
                inlines.append(
                    HyperlinkNode(id=self._rid(), target=target, runs=hl_runs)
                )
                text_parts.append("".join(hl_text))
        return inlines, "".join(text_parts)

    def _parse_run(self, r, base) -> Tuple[str, Optional[RunNode]]:
        parts: List[str] = []
        for sub in r:
            tag = etree.QName(sub).localname
            if tag == "t":
                parts.append(sub.text or "")
            elif tag == "tab":
                parts.append("\t")
            elif tag == "br":
                parts.append("\n")
        text = "".join(parts)
        if not text.strip():
            return "", None
        direct = parse_rpr(r.find(w("rPr")))
        props = dict(base)
        for k, v in direct.items():
            if v is not None:
                props[k] = v
        style = RunProperties(
            font=props.get("font"),
            font_size=props.get("size"),
            bold=props.get("bold"),
            italic=props.get("italic"),
        )
        return text, RunNode(id=self._rid(), text=text, style=style)

    # ============ 图片 ============
    def _extract_images(self, p) -> List[ImageNode]:
        out: List[ImageNode] = []
        for blip in p.iter(f"{{{A}}}blip"):
            embed = ra(blip, "embed")
            if not embed:
                continue
            drawing = blip.getparent()
            while drawing is not None and etree.QName(drawing).localname != "drawing":
                drawing = drawing.getparent()
            cx = cy = alt = fmt = None
            if drawing is not None:
                ext = drawing.find(f".//{{{WP}}}extent")
                if ext is not None:
                    cx, cy = ext.get("cx"), ext.get("cy")
                docpr = drawing.find(f".//{{{WP}}}docPr")
                if docpr is not None:
                    alt = docpr.get("descr") or docpr.get("name")
            if self._reader is not None:
                media = self._reader.media_for_rel(embed)
                if media is not None:
                    _, fmt = media
            out.append(
                ImageNode(
                    id=self._bid(),
                    image_id=embed,
                    format=fmt,
                    width_emu=int(cx) if cx and cx.isdigit() else None,
                    height_emu=int(cy) if cy and cy.isdigit() else None,
                    alt=alt,
                )
            )
        return out

    # ============ TOC（best-effort）============
    def _parse_table(self, tbl) -> TableNode:
        """解析表格：gridSpan→colspan，vMerge restart→rowspan（continue 单元格丢弃）。"""
        open_starts: dict = {}  # col_idx -> TableCellNode(restart，待累加)
        rows: List[TableRowNode] = []
        for tr in tbl.findall(w("tr")):
            keep: List[TableCellNode] = []
            col = 0
            for tc in tr.findall(w("tc")):
                colspan = self._colspan(tc)
                vmerge = self._vmerge_state(tc)
                cell = TableCellNode(
                    id=self._bid(),
                    blocks=self._parse_cell_blocks(tc),
                    colspan=colspan,
                    rowspan=1,
                )
                if vmerge == "restart":
                    open_starts[col] = cell
                    keep.append(cell)
                elif vmerge == "continue":
                    if col in open_starts:
                        open_starts[col].rowspan += 1
                    # continue 单元格并入上方 restart，本行丢弃
                else:
                    keep.append(cell)
                col += colspan
            is_header = False
            trpr = tr.find(w("trPr"))
            if trpr is not None and trpr.find(w("tblHeader")) is not None:
                is_header = True
            rows.append(TableRowNode(id=self._bid(), cells=keep, is_header=is_header))
        return TableNode(id=self._bid(), rows=rows)

    def _vmerge_state(self, tc) -> Optional[str]:
        tcpr = tc.find(w("tcPr"))
        if tcpr is None:
            return None
        vm = tcpr.find(w("vMerge"))
        if vm is None:
            return None
        return "restart" if wa(vm, "val") == "restart" else "continue"

    def _colspan(self, tc) -> int:
        tcpr = tc.find(w("tcPr"))
        if tcpr is None:
            return 1
        gs = tcpr.find(w("gridSpan"))
        if gs is None:
            return 1
        v = wa(gs, "val")
        return int(v) if v and v.isdigit() else 1

    def _parse_cell_blocks(self, tc) -> List[BlockNode]:
        blocks: List[BlockNode] = []
        for child in tc:
            tag = etree.QName(child).localname
            if tag == "p":
                kind, level, runs, text, list_info, images = self._classify(child)
                if images:
                    blocks.extend(images)
                if kind == "heading":
                    blocks.append(HeadingNode(id=self._bid(), level=level, text=text, runs=runs))
                elif text or runs:
                    blocks.append(ParagraphNode(id=self._bid(), runs=runs, text=text))
            elif tag == "tbl":
                blocks.append(self._parse_table(child))
        return blocks

    # ============ TOC（best-effort）============
    def _has_field_instr(self, p, keyword) -> bool:
        for it in p.iter(w("instrText")):
            if it.text and keyword.upper() in it.text.upper():
                return True
        return False

    def _has_field_end(self, p) -> bool:
        for fc in p.iter(w("fldChar")):
            if wa(fc, "fldCharType") == "end":
                return True
        return False

    def _collect_toc_entry(self, p) -> None:
        runs, text = self._parse_runs(p, self._paragraph_style(p))
        if text.strip():
            self._toc_entries.append(TocEntry(text=text.strip(), level=None))

    def _paragraph_style(self, p) -> Optional[str]:
        ppr = p.find(w("pPr"))
        if ppr is None:
            return None
        ps = ppr.find(w("pStyle"))
        return wa(ps, "val") if ps is not None else None
