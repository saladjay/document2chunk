# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step7a T2/T3 补测请求包：material → gen/requests_t2.jsonl。

为每张样本表截取前后各 ≤3 段散文（遇标题停，与真池切节同界），
供 LLM 生成两类问题（审核五层类型学的 T2/T3）：
- T2 联合推理：答案需要 表格数据 + 前后正文 共同得出（gold=表 chunk+节 chunk）
- T3 上下文定位：答案在正文中、表格仅作背景（gold=节 chunk；测表 chunk 干扰度）
表前后无散文的样本表跳过并计数（T2/T3 不可构造）。
"""
from __future__ import annotations

import json
from pathlib import Path

from gen_requests import grid_text

HERE = Path(__file__).parent
MAX_PARAS = 3
TEXT_TYPES = ("paragraph", "list", "formula")


def context_around(blocks: list[dict], bi: int) -> tuple[list[str], list[str]]:
    left: list[str] = []
    i = bi - 1
    while i >= 0 and len(left) < MAX_PARAS:
        bd = blocks[i]
        if bd["type"] == "heading":
            break
        if bd["type"] in TEXT_TYPES and bd.get("text", "").strip():
            left.append(bd["text"].strip())
        i -= 1
    left.reverse()
    right: list[str] = []
    i = bi + 1
    while i < len(blocks) and len(right) < MAX_PARAS:
        bd = blocks[i]
        if bd["type"] == "heading":
            break
        if bd["type"] in TEXT_TYPES and bd.get("text", "").strip():
            right.append(bd["text"].strip())
        i += 1
    return left, right


def main() -> None:
    out: list[dict] = []
    skipped = []
    for dp in sorted(HERE.glob("material/*.json")):
        doc = json.loads(dp.read_text(encoding="utf-8"))
        if not doc.get("samples"):
            continue
        blocks = doc["blocks"]
        ti = 0
        for bi, bd in enumerate(blocks):
            if bd["type"] != "table":
                continue
            key = f"{doc['file']}::{ti}"
            ti += 1
            if key not in doc["samples"]:
                continue
            hit = doc["samples"][key]
            left, right = context_around(blocks, bi)
            if not left and not right:
                skipped.append(key)
                continue
            out.append({
                "key": key,
                "role": hit["role"],
                "title": hit["title"],
                "caption": hit["caption"],
                "grid": grid_text(hit["table"]),
                "context_left": left,
                "context_right": right,
            })
    with (HERE / "gen" / "requests_t2.jsonl").open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_m = sum(1 for r in out if r["role"] == "merged")
    print(f"requests_t2={len(out)} (merged {n_m}, simple {len(out) - n_m}) "
          f"skipped_no_context={len(skipped)}")


if __name__ == "__main__":
    main()
