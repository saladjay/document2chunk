# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step2 抽样：scan_index.jsonl → sample.json。

40 含合并格（主样本）+ 20 简单表（对照），跨文档均匀（优先一文件一表）。
剔除退化：image_route / 非空格 < 6 / 单列 / 超长单元格 / 超大表。
随机种子固定 42，可复现。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
SEED = 42

FILTERS = {
    "min_nonempty": 6,
    "min_cols": 2,
    "max_rows": 40,
    "max_cell_len": 120,
    "min_cell_chars": 40,
}


def ok(rec: dict) -> bool:
    return (
        not rec.get("image_route")
        and rec["nonempty"] >= FILTERS["min_nonempty"]
        and rec["grid_cols"] >= FILTERS["min_cols"]
        and rec["rows"] <= FILTERS["max_rows"]
        and rec["max_cell_len"] <= FILTERS["max_cell_len"]
        and rec["cell_chars"] >= FILTERS["min_cell_chars"]
    )


def pick(cands: list[dict], n: int, rng: random.Random) -> list[dict]:
    """优先一文件一表；不足再允许同文件第二表。"""
    rng.shuffle(cands)
    chosen: list[dict] = []
    seen: set[str] = set()
    for r in cands:
        if r["file"] not in seen:
            chosen.append(r)
            seen.add(r["file"])
        if len(chosen) == n:
            return chosen
    for r in cands:
        if r not in chosen:
            chosen.append(r)
        if len(chosen) == n:
            break
    return chosen


def main() -> None:
    recs = [
        json.loads(ln)
        for ln in (HERE / "scan_index.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    merged = [r for r in recs if ok(r) and r["merged"]]
    simple = [r for r in recs if ok(r) and not r["merged"]]
    rng = random.Random(SEED)
    m40 = pick(merged, 40, rng)
    s20 = pick(simple, 20, rng)

    out = {
        "seed": SEED,
        "filters": FILTERS,
        "scan_totals": {
            "tables": len(recs),
            "merged_ok": len(merged),
            "simple_ok": len(simple),
        },
        "merged_sample": m40,
        "simple_sample": s20,
    }
    (HERE / "sample.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    files = {r["file"] for r in m40} | {r["file"] for r in s20}
    print(
        f"merged {len(m40)} (files {len({r['file'] for r in m40})}), "
        f"simple {len(s20)} (files {len({r['file'] for r in s20})}), "
        f"total files {len(files)}"
    )


if __name__ == "__main__":
    main()
