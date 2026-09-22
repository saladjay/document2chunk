# -*- coding: utf-8 -*-
"""查询集中立校验：validate_queries.py gen/queries_relay.json

检查键齐（60）、形齐（每 key 4 条）、反抄袭（查询×单元格 LCS≥6 字符）。
产出 gen/validate_<文件名>.json。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from merge_gen import lcs

HERE = Path(__file__).parent


def main() -> None:
    qpath = HERE / sys.argv[1]
    queries = json.loads(qpath.read_text(encoding="utf-8"))
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    cell_texts: dict[str, list[str]] = {}
    for dp in HERE.glob("material/*.json"):
        doc = json.loads(dp.read_text(encoding="utf-8"))
        for key, hit in doc["samples"].items():
            cell_texts[key] = [c["t"] for row in hit["table"]["rows"]
                               for c in row["cells"] if c["t"]]

    problems = [f"缺失 key: {k}" for k in sorted(set(manifest) - set(queries))]
    problems += [f"{k}: queries={len(v)}" for k, v in queries.items() if len(v) != 4]
    violations = []
    for key, qs in queries.items():
        for qi, q in enumerate(qs, 1):
            hit = max((lcs(q, ct) for ct in cell_texts.get(key, [])), default=0)
            if hit >= 6:
                violations.append({"key": key, "q": qi, "lcs": hit, "query": q})
    name = qpath.stem.replace("queries_", "")
    report = {"problems": problems, "verbatim_violations": violations}
    (HERE / "gen" / f"validate_{name}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{qpath.name}: keys={len(queries)}/60 problems={len(problems)} "
          f"verbatim_flagged={len(violations)}")


if __name__ == "__main__":
    main()
