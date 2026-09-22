# -*- coding: utf-8 -*-
"""生成 20% 人工抽查清单：主查询集（clean）→ gen/spotcheck_relay.md。

选取：全部「曾有反抄袭标记」的表 + 随机补足到 15 张（≈25%），seed=42。
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent


def main() -> None:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    queries = json.loads((HERE / "gen" / "queries_relay_clean.json").read_text(encoding="utf-8"))
    val = json.loads((HERE / "gen" / "validate_relay.json").read_text(encoding="utf-8"))
    dropped: dict[str, list[int]] = {}
    for v in val.get("verbatim_violations", []):
        dropped.setdefault(v["key"], []).append(v["q"])

    reqs = {}
    for ln in (HERE / "gen" / "requests.jsonl").read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            reqs[r["key"]] = r

    flagged = [k for k in queries if k in dropped]
    rest = [k for k in queries if k not in dropped]
    random.Random(42).shuffle(rest)
    picked = sorted(set(flagged + rest[: max(0, 15 - len(flagged))]))

    lines = ["# 表格嵌入格式实验 · 主查询集人工抽查清单", "",
             f"抽查 {len(picked)}/{len(queries)} 张表"
             f"（含全部 {len(flagged)} 张曾有反抄袭标记的表）；seed=42。", "",
             "逐条核对：问题是否可从该表作答、是否泄露答案、是否自然（会真么问吗）。"
             "有问题在行尾标 ❌+原因。", ""]
    for k in picked:
        r = reqs[k]
        lines += [f"## {k}", "",
                  f"- file: `{manifest[k]['file']}`", f"- role: {manifest[k]['role']}",
                  f"- 标题: {r['title'] or '（无）'}", f"- 曾被剔除的问号: {dropped.get(k, [])}", "",
                  "网格:", "", "```", r["grid"], "```", "", "查询（clean 后保留）:"]
        for i, q in enumerate(queries[k], 1):
            lines.append(f"{i}. {q}")
        lines.append("")
    (HERE / "gen" / "spotcheck_relay.md").write_text(
        "\n".join(lines), encoding="utf-8")
    print(f"spotcheck: {len(picked)} tables (flagged {len(flagged)}) -> gen/spotcheck_relay.md")


if __name__ == "__main__":
    main()
