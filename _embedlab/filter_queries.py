# -*- coding: utf-8 -*-
"""按反抄袭闸过滤查询集：queries_relay.json → queries_relay_clean.json。

剔除与单元格 LCS≥6 的查询；某表 4 问全灭则整表剔除（ gold 无从成立）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from merge_gen import lcs

HERE = Path(__file__).parent
SRC = sys.argv[1] if len(sys.argv) > 1 else "gen/queries_relay.json"
DST = sys.argv[2] if len(sys.argv) > 2 else "gen/queries_relay_clean.json"


def main() -> None:
    queries = json.loads((HERE / SRC).read_text(encoding="utf-8"))
    cell_texts: dict[str, list[str]] = {}
    for dp in HERE.glob("material/*.json"):
        doc = json.loads(dp.read_text(encoding="utf-8"))
        for key, hit in doc["samples"].items():
            cell_texts[key] = [c["t"] for row in hit["table"]["rows"]
                               for c in row["cells"] if c["t"]]

    clean: dict[str, list[str]] = {}
    dropped_q = dropped_tbl = 0
    for key, qs in queries.items():
        kept = []
        for q in qs:
            worst = max((lcs(q, ct) for ct in cell_texts.get(key, [])), default=0)
            if worst >= 6:
                dropped_q += 1
            else:
                kept.append(q)
        if kept:
            clean[key] = kept
        else:
            dropped_tbl += 1

    (HERE / DST).write_text(json.dumps(clean, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    n_q = sum(len(v) for v in clean.values())
    print(f"{SRC} -> {DST}: tables {len(clean)}/{len(queries)} "
          f"(drop {dropped_tbl}), queries {n_q}/{sum(len(v) for v in queries.values())} "
          f"(drop {dropped_q})")


if __name__ == "__main__":
    main()
