"""OOXML 嵌入物解析（阶段 B）：去重遍历 / 文本框 / OMML 公式 / OLE。

设计依据：docs/superpowers/specs/2026-08-17-docx-ooxml-coverage-design.md §3/§4。
"""

from __future__ import annotations

from typing import Iterator

from lxml import etree

_ALT_DEPTH_LIMIT = 10


def _choice_or_fallback(alt_el):
    """mc:AlternateContent → 首个 mc:Choice；无 Choice 走 mc:Fallback。"""
    target = None
    for sub in alt_el:
        if not isinstance(sub.tag, str):
            continue
        if etree.QName(sub).localname == "Choice":
            target = sub
            break
    if target is None:
        for sub in alt_el:
            if not isinstance(sub.tag, str):
                continue
            if etree.QName(sub).localname == "Fallback":
                target = sub
                break
    return target


def content_children(el, depth: int = 0) -> Iterator[etree._Element]:
    """直接子元素遍历；AlternateContent 替换为其 Choice/Fallback 的子元素。

    WPS 双写（12% 样本）：Choice(wps) 与 Fallback(VML) 各含一份内容，
    只走 Choice 防文字/图片双计。
    """
    if depth > _ALT_DEPTH_LIMIT:
        return
    for child in el:
        if not isinstance(child.tag, str):
            continue
        if etree.QName(child).localname == "AlternateContent":
            target = _choice_or_fallback(child)
            if target is not None:
                yield from content_children(target, depth + 1)
            continue
        yield child


def iter_content(el) -> Iterator[etree._Element]:
    """深度遍历子树（去重规则同 content_children）。"""
    for child in content_children(el):
        yield child
        yield from iter_content(child)


def inside_textbox(el) -> bool:
    """祖先链是否含 txbxContent（文本框内外判定）。"""
    a = el.getparent()
    while a is not None:
        if etree.QName(a).localname == "txbxContent":
            return True
        a = a.getparent()
    return False


# ============ OMML 公式（极简映射，未知结构降级文本拼接） ============


def _find_child(el, name: str):
    """按局部名取首个子元素（OMML 子元素固定顺序，局部名足够）。"""
    for child in el:
        if etree.QName(child).localname == name:
            return child
    return None


def omml_to_latex(el) -> str:
    """OMML → LaTeX 极简映射；未识别结构递归拼接子元素。"""
    if el is None:
        return ""
    ln = etree.QName(el).localname
    if ln == "t":
        return el.text or ""
    if ln == "f":
        return "\\frac{%s}{%s}" % (
            omml_to_latex(_find_child(el, "num")),
            omml_to_latex(_find_child(el, "den")),
        )
    if ln == "sSup":
        return "%s^{%s}" % (
            omml_to_latex(_find_child(el, "e")),
            omml_to_latex(_find_child(el, "sup")),
        )
    if ln == "sSub":
        return "%s_{%s}" % (
            omml_to_latex(_find_child(el, "e")),
            omml_to_latex(_find_child(el, "sub")),
        )
    if ln == "sSubSup":
        return "%s_{%s}^{%s}" % (
            omml_to_latex(_find_child(el, "e")),
            omml_to_latex(_find_child(el, "sub")),
            omml_to_latex(_find_child(el, "sup")),
        )
    if ln == "rad":
        deg = omml_to_latex(_find_child(el, "deg"))
        body = omml_to_latex(_find_child(el, "e"))
        return ("\\sqrt[%s]{%s}" % (deg, body)) if deg.strip() else ("\\sqrt{%s}" % body)
    if ln == "d":
        inner = "".join(
            omml_to_latex(c) for c in el if etree.QName(c).localname == "e"
        )
        return "(%s)" % inner
    return "".join(omml_to_latex(c) for c in el)


def omml_text(el) -> str:
    """纯文本兜底：子树内所有 m:t 拼接。"""
    if el is None:
        return ""
    return "".join(e.text or "" for e in el.iter() if etree.QName(e).localname == "t")
