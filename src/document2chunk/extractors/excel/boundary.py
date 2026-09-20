"""表边界：空行横切 + 段内空列纵切（难点2）。

坐标为实际扫描值域内的 0-based 闭区间；声明 dimension 不参与（定档 #5 修正）。
"""
from __future__ import annotations

from document2chunk.extractors.excel.models import Region, SheetGrid


def _occupied(v: object) -> bool:
    return v is not None and not (isinstance(v, str) and v.strip() == "")


def _cell(grid: SheetGrid, r: int, c: int) -> object:
    row = grid.values[r] if r < grid.n_rows else []
    return row[c] if c < len(row) else None


def _row_has_content(grid: SheetGrid, r: int) -> bool:
    return any(_occupied(_cell(grid, r, c)) for c in range(grid.n_cols))


def _col_has_content(grid: SheetGrid, r1: int, r2: int, c: int) -> bool:
    return any(_occupied(_cell(grid, r, c)) for r in range(r1, r2 + 1))


def split_regions(grid: SheetGrid) -> list[Region]:
    n_rows, n_cols = grid.n_rows, grid.n_cols
    regions: list[Region] = []
    r = 0
    while r < n_rows:
        if not _row_has_content(grid, r):
            r += 1
            continue
        band_start = r
        while r < n_rows and _row_has_content(grid, r):
            r += 1
        band_end = r - 1
        c = 0
        while c < n_cols:
            if not _col_has_content(grid, band_start, band_end, c):
                c += 1
                continue
            col_start = c
            while c < n_cols and _col_has_content(grid, band_start, band_end, c):
                c += 1
            regions.append(Region(band_start, col_start, band_end, c - 1))
    return regions
