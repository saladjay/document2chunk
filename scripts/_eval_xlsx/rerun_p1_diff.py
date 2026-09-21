"""P1 修复批重跑对比：本地解析 23 文件语料 vs D:\\output 基线（2026-09-21 grill Q10 护栏）。

- canary（13/14/15/20/23）：rows[].data/text 与 blocks 零差异（warnings/meta 允许新增）。
- 目标文件按文件名子串匹配（真实文件名不带评比序号，勿按序号映射）；22 号等 #N/A 断言依赖 openpyxl 错误格捕获（Task 4）。
- 02 号假 .xlsx 本地无 soffice → 跳过并标注「需 112 复验」。
用法：uv run python scripts/_eval_xlsx/rerun_p1_diff.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

XLSX_DIR = r"D:\document2chunk-test\xlsx"
BASELINE_DIR = r"D:\output"
REPORT = os.path.join(os.path.dirname(__file__), "p1_rerun_report.md")
CANARY_MARKS = ("百度千帆", "科技项目清单-20260204", "清单处理-完整填充", "组织页面数据", "-最新")


def _rule(stem: str) -> str | None:
    """评比序号 → 文件名子串（真实文件名不带评比序号，勿按序号映射）。"""
    if "智慧管养" in stem:
        return "04"
    if "广东省高速公路改扩建" in stem:
        return "05"
    if "边坡" in stem:
        return "07"
    if "科小星测试0926" in stem:
        return "09"
    if "平台操作常见问题" in stem:
        return "18"
    if "政策汇编" in stem:
        return "21"
    if "项目清单2026.3.3" in stem:
        return "22"
    return None


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _check(stem: str, base: dict | None, new: dict, err: str | None) -> tuple[str, list[str]]:
    if err:
        return ("SKIP 需112复验" if "无法解析" in err else f"ERROR {err}"), []
    fails: list[str] = []
    if base is None:
        # 无基线即该文件断言（含 canary 门）无处落地——按 ERROR 处理置 exit 1，勿静默放行
        return f"ERROR 无基线: {os.path.join(BASELINE_DIR, stem + '.json')}", []
    rule = _rule(stem)
    if any(m in stem for m in CANARY_MARKS):
        ob, nb = base["rows"], new["rows"]
        if len(ob) != len(nb):
            fails.append(f"canary 行数 {len(ob)}→{len(nb)}")
        else:
            for i, (a, b) in enumerate(zip(ob, nb)):
                if a["data"] != b["data"] or a["text"] != b["text"]:
                    fails.append(f"canary 第{i}行 data/text 变动")
                    break
        if [(b["sheet"], b["kind"], b["text"]) for b in base["blocks"]] != [
            (b["sheet"], b["kind"], b["text"]) for b in new["blocks"]
        ]:
            fails.append("canary blocks 变动")
    rows, blocks = new["rows"], new["blocks"]

    if rule == "22":
        na = sum(1 for r in rows for v in r["data"].values() if v == "#N/A")
        if na != 1462:
            fails.append(f"#N/A 恢复数 {na} != 1462")
        for ev in [r["meta"].get("total_evidence", []) for r in rows if r["meta"].get("from_total")]:
            if "keyword" not in ev:
                fails.append(f"from_total 无关键词证据: {ev}")
        if not any(r["meta"].get("from_total_candidate") for r in rows):
            fails.append("无 candidate 行（R6 应降级）")
    if rule == "05":
        hit = [
            r
            for r in rows
            if (r["meta"].get("from_total") or r["meta"].get("from_total_candidate"))
            and "1988.9156" in json.dumps(r["data"], ensure_ascii=False)
        ]
        if not hit:
            fails.append("侧表合计 1988.9156 未打任何标")
    if rule == "07":
        if not any(len(b["text"]) >= 500 for b in blocks):
            fails.append("填表说明大块未产出")
    if rule == "09":
        if not any("评分标准" in b["text"] or len(b["text"]) >= 200 for b in blocks):
            fails.append("评分标准整片未产出")
    if rule == "21":
        mx = max((len(k) for r in rows for k in r["data"]), default=0)
        if mx >= 100:
            fails.append(f"键最长 {mx} 字（表头污染仍在）")
    if rule == "04":
        # 标题 ≤20 字且合并带为宽 1 时走纯列表路线成「列1 行」——内容保全即达标（块或行均可）
        if not (
            any("单位：万元" in b["text"] for b in blocks)
            or any("单位：万元" in json.dumps(r["data"], ensure_ascii=False) for r in rows)
        ):
            fails.append("标题行未兜底（块与行均未见）")
        if not any(b["text"].startswith("注意") for b in blocks):
            fails.append("尾注未兜底")
    if rule == "18":
        if not any(
            b["meta"].get("role") == "sheet_header" for b in blocks
        ) and not any("第 1 行" in w for w in new["warnings"]):
            fails.append("R1 两格仍无踪影")
    # 全局铁律：definite 必须有关键词证据
    for ev in [r["meta"].get("total_evidence", []) for r in rows if r["meta"].get("from_total")]:
        if "keyword" not in ev:
            fails.append(f"from_total 无关键词证据: {ev}")
    return ("PASS" if not fails else "FAIL"), fails


def main() -> int:
    lines = ["# P1 修复批重跑报告", "", "| # | 文件 | 结论 | 说明 |", "|---|------|------|------|"]
    exit_code = 0
    for path in sorted(glob.glob(os.path.join(XLSX_DIR, "*.xlsx"))):
        name = os.path.basename(path)
        if name.startswith("~$"):
            continue
        stem = os.path.splitext(name)[0]
        base_p = os.path.join(BASELINE_DIR, stem + ".json")
        base = _load(base_p) if os.path.exists(base_p) else None
        err = None
        new = None
        try:
            from document2chunk.extractors.excel.parser import parse_excel_bytes
            from document2chunk.extractors.excel.serializer import to_envelope

            with open(path, "rb") as f:
                new = to_envelope(parse_excel_bytes(f.read(), name))
        except Exception as exc:  # noqa: BLE001 —— 02 号假 .xlsx 预期在此落下
            err = str(exc)
        status, fails = _check(stem, base, new or {}, err or None)
        detail = "; ".join(fails) if fails else (err or "")
        print(f"{status:14s} {stem[:40]:42s} {detail[:80]}")
        lines.append(f"| {stem.split('-')[0]} | {stem} | {status} | {detail} |")
        if status == "FAIL" or status.startswith("ERROR"):
            exit_code = 1
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告：{REPORT}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
