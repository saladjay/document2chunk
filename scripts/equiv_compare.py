"""equiv_compare —— 提效前后解析结果对照·比对端。

用法:
  python scripts/equiv_compare.py <baseline_root> <after_root>

读两边的 manifest.jsonl,按 file 字段配对,逐文件比对:
  result.md 的 md5、图片 (name, md5) 有序表、块数/章节数/分型计数、状态。
输出 PASS/FAIL 清单 + 汇总;FAIL 给出首个差异的具体内容。
有 FAIL 时退出码 1。耗时差异只报告不判失败(信息项)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load(root: Path) -> dict[str, dict]:
    entries = {}
    with open(root / "manifest.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                e = json.loads(line)
                entries[e["file"]] = e
    return entries


def first_image_diff(a: list, b: list) -> str:
    da, db = dict(map(tuple, a)), dict(map(tuple, b))
    for name in sorted(set(da) | set(db)):
        if name not in da:
            return f"图片多出: {name}"
        if name not in db:
            return f"图片缺失: {name}"
        if da[name] != db[name]:
            return f"图片内容变: {name} ({da[name][:8]} -> {db[name][:8]})"
    return "?"


def main() -> int:
    base_root, after_root = map(Path, sys.argv[1:3])
    base, after = load(base_root), load(after_root)

    files = sorted(set(base) | set(after))
    n_pass = n_fail = n_skip = 0
    fails: list[str] = []
    speedups: list[tuple[str, float, float]] = []

    for fp in files:
        name = Path(fp).name
        b, a = base.get(fp), after.get(fp)
        if b is None or a is None:
            print(f"MISS  {name}: 只在一侧出现(base={b is not None} after={a is not None})")
            n_fail += 1
            fails.append(f"MISS {name}")
            continue
        if b.get("status") == "SKIPPED_OCR" or a.get("status") == "SKIPPED_OCR":
            print(f"SKIP  {name}: OCR 路不对照 ({b.get('reason') or a.get('reason')})")
            n_skip += 1
            continue
        if b.get("status") != "OK" or a.get("status") != "OK":
            print(f"FAIL  {name}: status {b.get('status')} -> {a.get('status')}"
                  f"{'; ' + (a.get('reason') or '')[:160] if a.get('reason') else ''}")
            n_fail += 1
            fails.append(f"STATUS {name}")
            continue

        problems = []
        if b["md5_result_md"] != a["md5_result_md"]:
            problems.append(f"result.md 变了 ({b['md5_result_md'][:8]} -> {a['md5_result_md'][:8]})")
        if b["images"] != a["images"]:
            problems.append("图片表变了: " + first_image_diff(b["images"], a["images"]))
        for k in ("n_blocks", "n_sections", "type_counts", "source_type"):
            if b.get(k) != a.get(k):
                problems.append(f"{k}: {b.get(k)} -> {a.get(k)}")

        be, ae = b.get("elapsed_s"), a.get("elapsed_s")
        if be and ae and ae > 0:
            speedups.append((name, be, ae))

        if problems:
            n_fail += 1
            fails.append(name)
            print(f"FAIL  {name}:")
            for p in problems:
                print(f"      - {p}")
        else:
            n_pass += 1
            print(f"PASS  {name}  ({be}s -> {ae}s)")

    print(f"\n=== 汇总: PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip} ===")
    if speedups:
        ts, ta = sum(s[1] for s in speedups), sum(s[2] for s in speedups)
        print(f"耗时合计(可比文件): {ts:.1f}s -> {ta:.1f}s  ({ts / ta:.2f}x)" if ta else "")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
