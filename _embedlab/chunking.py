# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step4b 真池 chunker（material JSON → 池 chunk 列表）。

口径（设计文档 §5.4）：
- chunk = 内容块 + 前最近标题行（标题前缀，全变体恒定）
- 非表格块按节聚合，~700 字符滚动切分；表格块独立成 chunk（与下游 md RAG 常规切法同型）
- 被抽样表正文留空占位（按变体在评测期填充）；其余背景表用 V1（现状）
"""
from __future__ import annotations

import json
from pathlib import Path

from serialize import v1_html

HERE = Path(__file__).parent
MAX_SECTION_CHARS = 700


def _load_docs() -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(HERE.glob("material/*.json"))
    ]


def chunk_doc(doc: dict, sample_keys: set[str]) -> tuple[list[dict], dict]:
    """单文档 → chunks；返回 (chunks, {sample_key: {title, caption, table}})。"""
    chunks: list[dict] = []
    title = ""
    buf: list[str] = []
    tbl_idx = 0
    seq = 0
    stem = doc["file"]

    def flush_section():
        nonlocal buf, seq
        if buf:
            body = "\n".join(buf).strip()
            if body:
                chunks.append({
                    "id": f"{stem}::sec{seq}",
                    "kind": "section",
                    "title": title,
                    "text": (title + "\n" + body) if title else body,
                })
                seq += 1
            buf = []

    for bd in doc["blocks"]:
        btype = bd["type"]
        if btype == "heading":
            flush_section()
            title = bd["text"]
            continue
        if btype == "table":
            key = f"{doc['file']}::{tbl_idx}"
            tbl_idx += 1
            flush_section()
            if key in sample_keys:
                chunks.append({
                    "id": f"tbl::{key}",
                    "kind": "sample_table",
                    "title": title,
                    "sample_key": key,
                    "text": "",  # 变体填充占位
                })
            else:
                body = v1_html({"rows": bd["rows"]})
                chunks.append({
                    "id": f"{stem}::tbl{seq}",
                    "kind": "bg_table",
                    "title": title,
                    "text": (title + "\n" + body) if title else body,
                })
                seq += 1
            continue
        text = bd.get("text", "").strip()
        if not text:
            continue
        buf.append(text)
        if sum(len(x) for x in buf) >= MAX_SECTION_CHARS:
            flush_section()
    flush_section()
    return chunks, doc.get("samples", {})


def build_pool() -> tuple[list[dict], dict]:
    """全部文档 → (pool, samples)；pool 含空占位样本表 chunk。"""
    pool: list[dict] = []
    samples: dict = {}
    for doc in _load_docs():
        cs, hits = chunk_doc(doc, set(doc.get("samples", {}).keys()))
        pool.extend(cs)
        samples.update(hits)
    return pool, samples


if __name__ == "__main__":
    pool, samples = build_pool()
    (HERE / "pool_base.json").write_text(
        json.dumps(pool, ensure_ascii=False), encoding="utf-8"
    )
    kinds = {}
    for c in pool:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"pool={len(pool)} by_kind={kinds} samples={len(samples)}")
