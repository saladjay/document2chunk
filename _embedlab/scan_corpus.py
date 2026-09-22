# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step1 语料扫描：799 DOCX → 表格索引 JSONL。

产出 _embedlab/scan_index.jsonl，每行一个表格：
  {file, tbl_idx, merged, image_route, rows, grid_cols, nonempty,
   max_cell_len, cell_chars, title, caption}
只索引不落全文；抽样后由 build_material.py 对选中文档二次提取。
不递归单元格内嵌套表（罕见，注释声明）；附件不扫（池化阶段以文本并入）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from document2chunk.export._helpers import cell_text
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.ir.models import HeadingNode, TableNode
from document2chunk.structure import assemble

CORPUS = Path(r"D:\document2chunk-test\docx")
OUT = Path(__file__).parent / "scan_index.jsonl"


def grid_cols(table: TableNode) -> int:
    if not table.rows:
        return 0
    return sum(max(1, getattr(c, "colspan", 1)) for c in table.rows[0].cells)


def table_stats(block: TableNode) -> dict:
    nonempty = 0
    max_len = 0
    chars = 0
    merged = False
    for row in block.rows:
        for cell in row.cells:
            t = (cell_text(cell) or "").strip()
            if t:
                nonempty += 1
            if len(t) > max_len:
                max_len = len(t)
            chars += len(t)
            if getattr(cell, "colspan", 1) > 1 or getattr(cell, "rowspan", 1) > 1:
                merged = True
    return {
        "merged": merged,
        "rows": len(block.rows),
        "grid_cols": grid_cols(block),
        "nonempty": nonempty,
        "max_cell_len": max_len,
        "cell_chars": chars,
    }


def main() -> None:
    files = sorted(f for f in CORPUS.rglob("*.docx") if not f.name.startswith("~$"))
    ext = DocxExtractor()
    lines: list[str] = []
    crashes: list[dict] = []
    t0 = time.time()

    for i, f in enumerate(files, 1):
        try:
            result = ext.extract(str(f))
            doc = assemble(result)
            title = ""
            tbl_idx = 0
            for block in doc.content:
                if isinstance(block, HeadingNode):
                    title = block.text
                    continue
                if not isinstance(block, TableNode):
                    continue
                rec = {
                    "file": f.name,
                    "tbl_idx": tbl_idx,
                    "title": title,
                    "caption": (block.metadata or {}).get("caption") or "",
                    "image_route": bool(getattr(block, "table_image_id", None)),
                }
                tbl_idx += 1
                if rec["image_route"]:
                    lines.append(json.dumps(rec, ensure_ascii=False))
                    continue
                rec.update(table_stats(block))
                lines.append(json.dumps(rec, ensure_ascii=False))
        except Exception as e:  # 单文件崩溃不阻断扫描
            crashes.append({"file": f.name, "err": repr(e)[:200]})
        if i % 20 == 0:
            print(
                f"[{i}/{len(files)}] {time.time() - t0:.0f}s "
                f"tables={len(lines)} crashes={len(crashes)}",
                flush=True,
            )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT.parent / "scan_crashes.json").write_text(
        json.dumps(crashes, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    n_merged = sum(1 for ln in lines if '"merged": true' in ln)
    print(
        f"DONE files={len(files)} tables={len(lines)} merged={n_merged} "
        f"crashes={len(crashes)} {time.time() - t0:.0f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
