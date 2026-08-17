# -*- coding: utf-8 -*-
"""抽查探针：sdt 内容形态 / 文本框父链形态 / footnotes 空壳确认。"""

from __future__ import annotations

import sys
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
parser = etree.XMLParser(recover=True)


def localname(el) -> str:
    return etree.QName(el).localname if el is not None else "?"


def probe(path: Path) -> None:
    zf = zipfile.ZipFile(path)
    doc = etree.fromstring(zf.read("word/document.xml"), parser=parser)

    # ---- 1. sdt：父链 + sdtPr 类型 + 内容首段文本 ----
    sdt_kinds: Counter = Counter()
    sdt_text_samples: list[str] = []
    for sdt in doc.iter():
        if localname(sdt) != "sdt" or len(sdt_text_samples) >= 3:
            continue
        pr = sdt.find(f"{W}sdtPr")
        kind = "plain"
        if pr is not None:
            for ch in pr:
                ln = localname(ch)
                if ln != "id" and ln != "docPartObj":
                    kind = ln
                    break
            gal = pr.find(f"{W}docPartObj/{W}docPartGallery")
            if gal is not None:
                kind += f"/{gal.get(f'{W}val')}"
        content = sdt.find(f"{W}sdtContent")
        txt = "".join(content.itertext()).strip()[:50] if content is not None else ""
        sdt_kinds[kind] += 1
        if txt:
            sdt_text_samples.append(f"[{kind}] {txt!r}")

    # ---- 2. 文本框：父链形态（wps:txbx vs v:textbox）+ 是否在 AlternateContent 内 ----
    txbx_forms: Counter = Counter()
    for tb in doc.iter():
        if localname(tb) != "txbxContent":
            continue
        anc = [localname(a) for a in tb.iterancestors()][:6]
        form = "wps" if "txbx" in anc else ("vml" if "textbox" in anc else "other")
        in_alt = "AlternateContent" in anc
        txbx_forms[f"{form}{'/AltContent' if in_alt else ''}"] += 1

    # ---- 3. footnotes.xml 空壳确认 ----
    fn_note = ""
    try:
        fn = etree.fromstring(zf.read("word/footnotes.xml"), parser=parser)
        kinds = Counter(localname(c) and (c.get(f"{W}type") or "real") for c in fn)
        fn_note = f"footnotes.xml 子节点: {dict(kinds)}"
    except KeyError:
        fn_note = "无 footnotes.xml"

    # ---- 4. OLE 形态：w:object 的子元素 ----
    ole_children: Counter = Counter()
    for obj in doc.iter():
        if localname(obj) == "object":
            for ch in obj:
                ln = localname(ch)
                ole_children[ln] += 1
            if sum(ole_children.values()) > 50:
                break

    name = path.name[:40]
    print(f"### {name}")
    if sdt_kinds:
        print(f"  sdt: {dict(sdt_kinds)}")
        for s in sdt_text_samples:
            print(f"    样例: {s}")
    if txbx_forms:
        print(f"  txbx: {dict(txbx_forms)}")
    if ole_children:
        print(f"  ole children: {dict(ole_children)}")
    print(f"  {fn_note}")


def main() -> None:
    root = Path(sys.argv[1])
    # 各取若干代表性文件：有 sdt 的、有 txbx 的、有 OLE 的
    picked: dict[str, list[Path]] = {"sdt": [], "txbx": [], "ole": [], "fn": []}
    for p in sorted(root.rglob("*.docx")):
        try:
            data = zipfile.ZipFile(p).read("word/document.xml")
        except Exception:  # noqa: BLE001
            continue
        doc = etree.fromstring(data, parser=parser)
        lns = {localname(e) for e in doc.iter()}
        names = zipfile.ZipFile(p).namelist()
        if "sdt" in lns and len(picked["sdt"]) < 8:
            picked["sdt"].append(p)
        if "txbxContent" in lns and len(picked["txbx"]) < 6:
            picked["txbx"].append(p)
        if "object" in lns and len(picked["ole"]) < 4:
            picked["ole"].append(p)
        if "word/footnotes.xml" in names and "endnoteReference" in lns and len(picked["fn"]) < 3:
            picked["fn"].append(p)

    for key, paths in picked.items():
        print(f"\n===== {key} 样本 =====")
        for p in paths:
            probe(p)


if __name__ == "__main__":
    main()
