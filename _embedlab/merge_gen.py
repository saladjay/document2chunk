# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step5b 批次合并 + 校验：gen_batch*.json → queries/summaries.json。

校验三关：
1. 键齐：60 个 key 与 manifest 完全一致；
2. 形齐：每 key 恰 4 条查询、摘要有内容；
3. 反抄袭：任一查询与该表任一单元格文本的最长公共子串 ≥6（CJK 判定按字符计）
   → 记违规，不阻塞，但进人工抽查清单（Q11 的 20% 抽查由此扩容）。
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def lcs(a: str, b: str) -> int:
    """最长公共子串长度（滚动 DP）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def main() -> None:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    queries: dict[str, list[str]] = {}
    summaries: dict[str, str] = {}
    problems: list[str] = []

    for bp in sorted(HERE.glob("gen/gen_batch*.json")):
        data = json.loads(bp.read_text(encoding="utf-8"))
        for key, val in data.items():
            if key not in manifest:
                problems.append(f"{bp.name}: 未知 key {key!r}")
                continue
            qs = val.get("queries", [])
            if len(qs) != 4:
                problems.append(f"{key}: queries={len(qs)} != 4")
            queries[key] = qs
            summaries[key] = (val.get("summary") or "").strip()

    missing = set(manifest) - set(queries)
    for k in sorted(missing):
        problems.append(f"缺失 key: {k}")

    # 反抄袭：查询单元格文本取自 material
    violations = []
    cell_texts: dict[str, list[str]] = {}
    for dp in HERE.glob("material/*.json"):
        doc = json.loads(dp.read_text(encoding="utf-8"))
        for key, hit in doc["samples"].items():
            cells = [
                c["t"]
                for row in hit["table"]["rows"]
                for c in row["cells"]
                if c["t"]
            ]
            cell_texts[key] = cells
    for key, qs in queries.items():
        for qi, q in enumerate(qs, 1):
            for ct in cell_texts.get(key, []):
                n = lcs(q, ct)
                if n >= 6:
                    violations.append(
                        {"key": key, "q": qi, "lcs": n, "cell": ct[:40], "query": q}
                    )
                    break

    (HERE / "gen" / "queries.json").write_text(
        json.dumps(queries, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (HERE / "gen" / "summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    report = {"problems": problems, "verbatim_violations": violations}
    (HERE / "gen" / "validate_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"keys={len(queries)}/60 summaries={len(summaries)} "
        f"problems={len(problems)} verbatim_flagged={len(violations)}"
    )


if __name__ == "__main__":
    main()
