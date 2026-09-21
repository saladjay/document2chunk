"""表头路径扁平化（Q7）+ 合并单元格值下放（Q4，难点1）。"""
from __future__ import annotations

import re

from document2chunk.extractors.excel.models import Region, SheetGrid

_WS = re.compile(r"\s+")


def clean_key(v: object) -> str:
    """表头清洗：压空白（含换行）为单空格、去首尾。"""
    return _WS.sub(" ", str(v)).strip()


def build_merge_origin(grid: SheetGrid) -> dict[tuple[int, int], tuple[int, int]]:
    """cell → 合并区左上角坐标。左上角自身与非合并格不入场。"""
    origin: dict[tuple[int, int], tuple[int, int]] = {}
    for r1, c1, r2, c2 in grid.merged:
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1):
                    origin[(r, c)] = (r1, c1)
    return origin


def cell_value(grid: SheetGrid, origin: dict, r: int, c: int) -> object:
    orow, ocol = origin.get((r, c), (r, c))
    row = grid.values[orow] if orow < grid.n_rows else []
    return row[ocol] if ocol < len(row) else None


def flatten_header(grid: SheetGrid, region: Region, header_rows: int) -> dict[int, str]:
    """列号 → 扁平化键。多级 `-` 连接（层内去重、空层跳过）；空表头 `列N`；撞名 `（2）`。"""
    origin = build_merge_origin(grid)
    keys: dict[int, str] = {}
    seen: dict[str, int] = {}
    for c in range(region.c1, region.c2 + 1):
        parts: list[str] = []
        for r in range(region.r1, region.r1 + header_rows):
            v = cell_value(grid, origin, r, c)
            if v is None:
                continue
            s = clean_key(v)
            if s and s not in parts:
                parts.append(s)
        key = "-".join(parts) if parts else f"列{c + 1}"
        n = seen.get(key, 0) + 1
        seen[key] = n
        if n > 1:
            key = f"{key}（{n}）"
        keys[c] = key
    return keys


def fill_region_values(
    grid: SheetGrid, region: Region, header_rows: int
) -> list[dict[int, object]]:
    """数据区逐行 → {列号: 原始值}。合并单元格值下放每行（Q4）；空值不入场。"""
    origin = build_merge_origin(grid)
    out: list[dict[int, object]] = []
    for r in range(region.r1 + header_rows, region.r2 + 1):
        rec: dict[int, object] = {}
        for c in range(region.c1, region.c2 + 1):
            v = cell_value(grid, origin, r, c)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                err = grid.error_cells.get((r, c))
                if err:
                    rec[c] = err
                continue
            rec[c] = v
        out.append(rec)
    return out
