# -*- coding: utf-8 -*-
"""真实公文样本人工抽查（阶段 B 验收：每特性挑 1 个样本导出 markdown 头部）。

用法: uv run python scripts/spotcheck_docx_ooxml.py D:/document2chunk-test/docx
人工核对：红头在文档头部 / OLE 占位 / 尾注在末尾 / 公式 latex / 无 Fallback 双份文字。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from lxml import etree

from document2chunk.export import to_markdown
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.structure import assemble

WANT = {
    "txbxContent": None,
    "oMath": None,
    "object": None,
    "endnoteReference": None,
    "AlternateContent": None,
}


def main() -> None:
    parser = etree.XMLParser(recover=True)
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    for p in sorted(root.rglob("*.docx")):
        try:
            doc = etree.fromstring(zipfile.ZipFile(p).read("word/document.xml"), parser=parser)
        except Exception:  # noqa: BLE001
            continue
        lns = {etree.QName(e).localname for e in doc.iter()}
        for feat in list(WANT):
            if WANT[feat] is None and feat in lns:
                WANT[feat] = p
        if all(WANT.values()):
            break
    for feat, p in WANT.items():
        if p is None:
            print(f"\n########## {feat}: 无样本")
            continue
        print(f"\n\n########## {feat} → {p.name}")
        result = DocxExtractor().extract(str(p))
        md = to_markdown(assemble(result))
        print(md[:2000])


if __name__ == "__main__":
    main()
