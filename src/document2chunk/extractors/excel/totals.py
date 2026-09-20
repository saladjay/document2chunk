"""合计行/小计行识别（轨 A 打标；轨 B 二期据 same 函数剔除）。

两层信号（text2sql 调研 §3.3 的 v1 子集）：
1. 行首列（第一非空单元格）命中词表——只匹配行首列，防"合计金额"列名误杀；
2. 校验和——行内数值列全部等于上方未标记数据行之和（容差相对 1e-6）。
"""
from __future__ import annotations

from document2chunk.extractors.excel.models import Region, SheetGrid

TOTAL_WORDS = ("合计", "总计", "小计", "汇总", "累计")
_TOTAL_EN = {"total", "subtotal", "grand total"}
_REL_TOL = 1e-6


def _is_total_label(v: object) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    return any(s == w or s.startswith(w) for w in TOTAL_WORDS) or s.lower() in _TOTAL_EN


def _numeric(grid: SheetGrid, r: int, c: int) -> object:
    row = grid.values[r] if r < grid.n_rows else []
    v = row[c] if c < len(row) else None
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def mark_total_rows(grid: SheetGrid, region: Region, header_rows: int) -> dict[int, str]:
    data_rows = list(range(region.r1 + header_rows, region.r2 + 1))
    marks: dict[int, str] = {}
    for r in data_rows:
        row = grid.values[r] if r < grid.n_rows else []
        ne = [v for v in row if v is not None and not (isinstance(v, str) and v.strip() == "")]
        if ne and _is_total_label(ne[0]):
            marks[r] = "keyword"

    numeric_cols = sorted(
        {
            c
            for r in data_rows
            for c, v in enumerate(
                grid.values[r] if r < grid.n_rows else []
            )
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    )
    for r in data_rows:
        if r in marks:
            continue
        checked = hits = 0
        for c in numeric_cols:
            v = _numeric(grid, r, c)
            if v is None:
                continue
            col_sum = sum(
                s
                for rr in data_rows
                if rr < r and rr not in marks and (s := _numeric(grid, rr, c)) is not None
            )
            checked += 1
            if abs(v - col_sum) <= _REL_TOL * max(1.0, abs(col_sum)):
                hits += 1
        if checked > 0 and hits == checked:
            marks[r] = "checksum"
    return marks
