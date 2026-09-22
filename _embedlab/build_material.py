# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step3 材料二次提取：sample.json 选中的文档 → material/*.json。

项目 venv 运行（uv run），复用管线解析；产出简化块级 JSON 供 lab 侧（无项目依赖）
做序列化与池化。tbl_idx 口径与 scan_corpus.py 一致：顶层 TableNode 顺序计数。
"""
from __future__ import annotations

import json
from pathlib import Path

from document2chunk.export._helpers import cell_text
from document2chunk.extractors.docx import DocxExtractor
from document2chunk.ir.models import (
    FormulaNode,
    HeadingNode,
    ListNode,
    ParagraphNode,
    TableNode,
)
from document2chunk.structure import assemble

HERE = Path(__file__).parent
CORPUS = Path(r"D:\document2chunk-test\docx")
MATERIAL = HERE / "material"


def table_dict(block: TableNode) -> dict:
    return {
        "rows": [
            {
                "is_header": bool(row.is_header),
                "cells": [
                    {
                        "t": (cell_text(c) or "").strip(),
                        "c": int(getattr(c, "colspan", 1) or 1),
                        "r": int(getattr(c, "rowspan", 1) or 1),
                    }
                    for c in row.cells
                ],
            }
            for row in block.rows
        ]
    }


def block_dict(block) -> dict | None:
    if isinstance(block, HeadingNode):
        return {"type": "heading", "level": block.level, "text": block.text}
    if isinstance(block, ParagraphNode):
        return {"type": "paragraph", "text": block.text} if block.text.strip() else None
    if isinstance(block, TableNode):
        return {
            "type": "table",
            "image_route": bool(getattr(block, "table_image_id", None)),
            "caption": (block.metadata or {}).get("caption") or "",
            **table_dict(block),
        }
    if isinstance(block, ListNode):
        lines = []
        for item in block.items:
            prefix = f"{item.level * 2}{' ' * item.level}"  # 缩进占位
            texts = []
            for sub in item.blocks:
                if isinstance(sub, ParagraphNode):
                    texts.append(sub.text)
                elif isinstance(sub, TableNode):
                    texts.append(table_text_plain(sub))
            if texts:
                lines.append(("  " * item.level) + "- " + " ".join(texts))
        return {"type": "list", "text": "\n".join(lines)} if lines else None
    if isinstance(block, FormulaNode):
        return {"type": "formula", "text": block.latex or block.text or ""}
    return None  # image/toc 等不入池（正文文本无损，图片缺文本不参与召回）


def table_text_plain(block: TableNode) -> str:
    return " ".join(
        (cell_text(c) or "").strip() for row in block.rows for c in row.cells
    )


def dump_doc(doc, wanted: dict) -> tuple[list[dict], dict]:
    """walk 顶层块 → (块列表, 本文档命中的样本表 {tbl_idx: {title, caption, table}})。"""
    blocks: list[dict] = []
    hits: dict[int, dict] = {}
    title = ""
    tbl_idx = 0
    for block in doc.content:
        if isinstance(block, HeadingNode):
            title = block.text
        bd = block_dict(block)
        if bd is None:
            continue
        if bd["type"] == "table":
            if tbl_idx in wanted:
                hits[tbl_idx] = {
                    "title": title,
                    "caption": bd["caption"],
                    "table": {
                        "rows": bd["rows"],
                    },
                }
            tbl_idx += 1
        blocks.append(bd)
    return blocks, hits


def main() -> None:
    sample = json.loads((HERE / "sample.json").read_text(encoding="utf-8"))
    wanted_by_file: dict[str, dict[int, str]] = {}
    for role, key in (("merged", "merged_sample"), ("simple", "simple_sample")):
        for rec in sample[key]:
            wanted_by_file.setdefault(rec["file"], {})[rec["tbl_idx"]] = role

    MATERIAL.mkdir(exist_ok=True)
    manifest: dict[str, dict] = {}
    ext = DocxExtractor()

    for fi, (fname, wanted) in enumerate(sorted(wanted_by_file.items()), 1):
        src = CORPUS / fname
        result = ext.extract(str(src))
        doc = assemble(result)
        stem = src.stem[:80].replace(" ", "_")
        blocks, hits = dump_doc(doc, set(wanted.keys()))

        hit_out = {}
        for tbl_idx, role in wanted.items():
            if tbl_idx not in hits:  # 挂图路由等与扫描口径漂移的兜底
                print(f"WARN {fname} tbl{tbl_idx} 未命中，跳过", flush=True)
                continue
            key = f"{fname}::{tbl_idx}"
            hit_out[key] = {"role": role, **hits[tbl_idx]}
            manifest[key] = {"file": fname, "stem": stem, "tbl_idx": tbl_idx, "role": role}

        (MATERIAL / f"{stem}.json").write_text(
            json.dumps({"file": fname, "blocks": blocks, "samples": hit_out},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[{fi}/{len(wanted_by_file)}] {fname[:50]} blocks={len(blocks)} hits={len(hit_out)}",
              flush=True)

    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"DONE docs={len(wanted_by_file)} sample_tables={len(manifest)}")


if __name__ == "__main__":
    main()
