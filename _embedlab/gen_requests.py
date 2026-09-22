# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step5a 生成请求包：material 样本表 → gen/requests.jsonl。

每行一个待生成任务：{key, role, title, caption, grid}，grid 是带 span 标记的
紧凑网格文本，供 LLM（或会话模型）生成 4 查询 + V6 摘要。
"""
from __future__ import annotations

import json
from pathlib import Path

from serialize import expand_grid

HERE = Path(__file__).parent


def grid_text(table: dict) -> str:
    grid, ncols = expand_grid(table)
    if not ncols:
        return "(空表)"
    lines = []
    for row in grid:
        lines.append("| " + " | ".join(v or "·" for v in row) + " |")
    return "\n".join(lines)


def main() -> None:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    out = []
    for doc_path in sorted(HERE.glob("material/*.json")):
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        for key, hit in doc["samples"].items():
            out.append({
                "key": key,
                "role": hit["role"],
                "file": manifest[key]["file"],
                "title": hit["title"],
                "caption": hit["caption"],
                "grid": grid_text(hit["table"]),
            })
    gdir = HERE / "gen"
    gdir.mkdir(exist_ok=True)
    with (gdir / "requests.jsonl").open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_m = sum(1 for r in out if r["role"] == "merged")
    print(f"requests={len(out)} merged={n_m} simple={len(out) - n_m}")


if __name__ == "__main__":
    main()
