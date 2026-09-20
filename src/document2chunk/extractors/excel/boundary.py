"""表边界：空行横切 + 段内空列纵切（难点2）+ 碎片回并（修正规则②）。

坐标为实际扫描值域内的 0-based 闭区间；声明 dimension 不参与（定档 #5 修正）。
"""
from __future__ import annotations

from document2chunk.extractors.excel.models import Region, SheetGrid

# 修正规则②：一条链上 ≥3 片（恰好隔 1 空行、列跨度重叠）判为"被空行碎切的同一张表"回并。
# 链长 2 的两张独立表保持分离（结构上不可区分，保守不合并）——2026-09-20 112 实测确立。
MIN_CHAIN = 3


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
    return merge_stacked_regions(grid, regions)


def merge_stacked_regions(
    grid: SheetGrid, regions: list[Region], *, min_chain: int = MIN_CHAIN
) -> list[Region]:
    """回并被全空行碎切的同一张表（112 实测：评审表每条记录占 3 行、行间全空行，
    34 条记录被切成 34 片、每片误立表头）。

    链条件：与链尾恰好隔 1 个全空行（reg.r1 == last.r2 + 2）且列跨度与链跨度重叠。
    链长 ≥ min_chain 才回并；不足则原样保留（两张独立表仅隔 1 空行时结构不可区分，保守拆分）。
    """
    if len(regions) < min_chain:
        return regions
    ordered = sorted(regions, key=lambda x: (x.r1, x.c1))
    chains: list[list[Region]] = []
    for reg in ordered:
        if chains:
            chain = chains[-1]
            span_c1 = min(x.c1 for x in chain)
            span_c2 = max(x.c2 for x in chain)
            last_r2 = max(x.r2 for x in chain)
            vertical = reg.r1 - last_r2 == 2 and reg.c1 <= span_c2 and reg.c2 >= span_c1
            # 同带列碎片：与链尾同行（或行区间重叠）且列跨度完全落在链跨度内
            sibling = (
                reg.r1 <= last_r2
                and span_c1 <= reg.c1
                and reg.c2 <= span_c2
            )
            if vertical or sibling:
                chain.append(reg)
                continue
        chains.append([reg])
    out: list[Region] = []
    for chain in chains:
        if len(chain) >= min_chain:
            out.append(
                Region(
                    min(x.r1 for x in chain),
                    min(x.c1 for x in chain),
                    max(x.r2 for x in chain),
                    max(x.c2 for x in chain),
                )
            )
        else:
            out.extend(chain)
    return out
