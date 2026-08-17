# -*- coding: utf-8 -*-
"""一次性扫描：真实公文 docx 样本的 OOXML 特性出现频率。

用法: uv run python scripts/scan_docx_features.py <样本目录>

按局部名（localname）匹配元素，规避 WPS/旧版 Word 的命名空间前缀怪癖。
产出 markdown 频率表（进设计文档）+ 逐文件明细。
"""

from __future__ import annotations

import sys
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

# document.xml / header / footer 等 body 类 part 里计数的元素局部名
ELEMENT_FEATURES = [
    "txbxContent",      # 文本框内容（VML 与 DrawingML 形态最终都含它）
    "pict",             # VML 图形容器（w:pict）
    "drawing",          # DrawingML 图形容器（w:drawing）
    "AlternateContent", # mc:AlternateContent（WPS/兼容模式双写）
    "object",           # OLE 嵌入（w:object）
    "OLEObject",        # o:OLEObject（VML 内 OLE 声明）
    "imagedata",        # v:imagedata（VML 图片引用，常伴 OLE 预览图）
    "oMath",            # OMML 行内公式
    "oMathPara",        # OMML 公式段
    "footnoteReference",
    "endnoteReference",
    "footnoteRef",
    "endnoteRef",
    "tbl",
    "sdt",
    "ins",              # 修订插入
    "del",              # 修订删除
    "commentReference",
]

# 部件级特性：part 名模式 → 特性名
PART_PATTERNS = [
    ("header_part", lambda n: n.startswith("word/header")),
    ("footer_part", lambda n: n.startswith("word/footer")),
    ("footnotes_xml", lambda n: n == "word/footnotes.xml"),
    ("endnotes_xml", lambda n: n == "word/endnotes.xml"),
    ("comments_xml", lambda n: n == "word/comments.xml"),
    ("embeddings", lambda n: n.startswith("word/embeddings/")),
    ("media_emf", lambda n: n.startswith("word/media/") and n.lower().endswith((".emf", ".wmf"))),
    ("media_bitmap", lambda n: n.startswith("word/media/") and n.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"))),
]

BODY_PARTS = ("word/document.xml",)  # 页眉页脚单独扫


def text_len(elem) -> int:
    """非空白字符数。"""
    return len("".join(elem.itertext()).strip()) if elem is not None else 0


def scan_file(path: Path) -> dict:
    info: dict = {"file": path.name, "size": path.stat().st_size, "elements": Counter(),
                  "parts": Counter(), "header_text_len": 0, "footer_text_len": 0,
                  "footnote_count": 0, "endnote_count": 0, "generator": "", "errors": []}
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:  # noqa: BLE001
        info["errors"].append(f"open: {e}")
        return info

    names = zf.namelist()
    for feat, match in PART_PATTERNS:
        n = sum(1 for nm in names if match(nm))
        if n:
            info["parts"][feat] = n

    parser = etree.XMLParser(recover=True)

    def parse_part(name: str):
        try:
            data = zf.read(name)
        except KeyError:
            return None
        try:
            return etree.fromstring(data, parser=parser)
        except Exception as e:  # noqa: BLE001
            info["errors"].append(f"{name}: {e}")
            return None

    # document.xml 元素计数
    doc = parse_part("word/document.xml")
    if doc is not None:
        for el in doc.iter():
            ln = etree.QName(el).localname
            if ln in ELEMENT_FEATURES:
                info["elements"][ln] += 1

    # 页眉页脚：元素计数 + 文本量（红头可能在这里）
    for nm in names:
        if nm.startswith("word/header"):
            el = parse_part(nm)
            info["header_text_len"] += text_len(el)
            if el is not None:
                for sub in el.iter():
                    ln = etree.QName(sub).localname
                    if ln in ELEMENT_FEATURES:
                        info["elements"]["hdr_" + ln] += 1
        elif nm.startswith("word/footer"):
            el = parse_part(nm)
            info["footer_text_len"] += text_len(el)

    # 脚注/尾注 part：真实条目数（排除 separator/continuationSeparator）
    fn = parse_part("word/footnotes.xml")
    if fn is not None:
        info["footnote_count"] = sum(
            1 for f in fn
            if etree.QName(f).localname == "footnote"
            and f.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type") is None
        )
    en = parse_part("word/endnotes.xml")
    if en is not None:
        info["endnote_count"] = sum(
            1 for f in en
            if etree.QName(f).localname == "endnote"
            and f.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type") is None
        )

    # 生成器（docProps/app.xml 的 Application）
    app = parse_part("docProps/app.xml")
    if app is not None:
        for el in app.iter():
            if etree.QName(el).localname == "Application" and el.text:
                info["generator"] = el.text.strip()
                break

    return info


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() == ".docx")
    if not files:
        print(f"!! 目录下无 .docx: {root}")
        return

    results = [scan_file(p) for p in files]
    n = len(results)

    # ---- 逐文件明细 ----
    print(f"## 逐文件明细（{n} 个 .docx）\n")
    print("| 文件 | 生成器 | txbx | pict | draw | AltC | obj/OLE | oMath | fnRef | enRef | 脚注part | 尾注part | 页眉x/字 | 页脚字 | 媒体(emf/bmp) | 嵌入 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        e, p = r["elements"], r["parts"]
        hdr_n = p.get("header_part", 0)
        print(
            f"| {r['file']} | {r['generator'][:20]} "
            f"| {e.get('txbxContent', 0) + e.get('hdr_txbxContent', 0)} "
            f"| {e.get('pict', 0)} | {e.get('drawing', 0)} | {e.get('AlternateContent', 0)} "
            f"| {e.get('object', 0)}/{e.get('OLEObject', 0)} "
            f"| {e.get('oMath', 0) + e.get('oMathPara', 0)} "
            f"| {e.get('footnoteReference', 0)} | {e.get('endnoteReference', 0)} "
            f"| {r['footnote_count']} | {r['endnote_count']} "
            f"| {hdr_n}/{r['header_text_len']} | {r['footer_text_len']} "
            f"| {p.get('media_emf', 0)}/{p.get('media_bitmap', 0)} | {p.get('embeddings', 0)} |"
        )
        if r["errors"]:
            print(f"  errors: {r['file']}: {r['errors']}")

    # ---- 汇总频率表 ----
    print(f"\n## 特性频率汇总（共 {n} 个样本）\n")
    print("| 特性 | 出现文件数 | 占比 | 总次数 |")
    print("|---|---|---|---|")

    def agg(key, counter_key=None):
        files_with = sum(1 for r in results if (r["elements"].get(counter_key or key, 0) > 0
                        or (counter_key is None and r["elements"].get(key, 0) > 0)))
        total = sum(r["elements"].get(counter_key or key, 0) for r in results)
        return files_with, total

    rows = []
    for label, getter in [
        ("文本框 txbxContent", lambda: agg("txbxContent")),
        ("页眉内文本框", lambda: agg("hdr_txbxContent")),
        ("VML 图形 w:pict", lambda: agg("pict")),
        ("DrawingML w:drawing", lambda: agg("drawing")),
        ("AlternateContent 双写", lambda: agg("AlternateContent")),
        ("OLE w:object", lambda: agg("object")),
        ("OLE o:OLEObject", lambda: agg("OLEObject")),
        ("OMML oMath(含Para)", lambda: agg("oMath")),
        ("脚注引用 fnRef", lambda: agg("footnoteReference")),
        ("尾注引用 enRef", lambda: agg("endnoteReference")),
        ("修订 ins", lambda: agg("ins")),
        ("修订 del", lambda: agg("del")),
        ("批注引用", lambda: agg("commentReference")),
        ("内容控件 sdt", lambda: agg("sdt")),
        ("表格 tbl", lambda: agg("tbl")),
    ]:
        fw, total = getter()
        rows.append((label, fw, total))

    for label, feat, match in [
        ("页眉 part", "header_part", lambda nm: nm.startswith("word/header")),
        ("页脚 part", "footer_part", lambda nm: nm.startswith("word/footer")),
        ("footnotes.xml", "footnotes_xml", lambda nm: nm == "word/footnotes.xml"),
        ("endnotes.xml", "endnotes_xml", lambda nm: nm == "word/endnotes.xml"),
        ("comments.xml", "comments_xml", lambda nm: nm == "word/comments.xml"),
        ("媒体 EMF/WMF", "media_emf", lambda nm: nm.startswith("word/media/") and nm.lower().endswith((".emf", ".wmf"))),
        ("媒体位图", "media_bitmap", lambda nm: nm.startswith("word/media/") and nm.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff"))),
        ("embeddings(OLE)", "embeddings", lambda nm: nm.startswith("word/embeddings/")),
    ]:
        fw = sum(1 for r in results if r["parts"].get(feat, 0) > 0)
        total = sum(r["parts"].get(feat, 0) for r in results)
        rows.append((label, fw, total))

    # 补充：脚注 part 有真实条目、页眉有实际文本
    rows.append(("页眉含文本(≥10字)", sum(1 for r in results if r["header_text_len"] >= 10), sum(r["header_text_len"] for r in results if r["header_text_len"] >= 10)))
    rows.append(("脚注part含真实条目", sum(1 for r in results if r["footnote_count"] > 0), sum(r["footnote_count"] for r in results)))

    rows.sort(key=lambda x: -x[1])
    for label, fw, total in rows:
        print(f"| {label} | {fw}/{n} | {fw / n:.0%} | {total} |")

    gens = Counter(r["generator"] or "?" for r in results)
    print("\n## 生成器分布\n")
    for g, c in gens.most_common():
        print(f"- {g}: {c}")


if __name__ == "__main__":
    main()
