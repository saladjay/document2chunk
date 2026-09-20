"""表头检测（难点1/3）与 sheet 路线分流（N5/N6，调研报告 §3.6）。

- 表头 = 顶部连续全字符串行（AAAI 类型同质性），上限 5（真实语料最多 3 级）。
- 下方无类型对比（全表皆字符串）时表头封顶 1 行——防吞纯文本数据区（盘点修正③）。
- 分流：文档型（≤2 列全字符串含长文本）、表单式（表头区上方有 ≥80% 宽的横向大合并抬头）。
"""
from __future__ import annotations

from document2chunk.extractors.excel.models import Region, SheetGrid, SheetRoute

MAX_HEADER_ROWS = 5
_DOC_MAX_COLS = 2
_DOC_MIN_LONG = 20
_FORM_BANNER_RATIO = 0.5  # ≥50% 宽合并视为分组/横幅（降自 0.8，捕获多级分组表头）


def _nonempty(cells: list[object]) -> list[object]:
    return [v for v in cells if v is not None and not (isinstance(v, str) and v.strip() == "")]


def _row_values(grid: SheetGrid, r: int, c1: int, c2: int) -> list[object]:
    row = grid.values[r] if r < grid.n_rows else []
    return [(row[c] if c < len(row) else None) for c in range(c1, c2 + 1)]


def _all_string(cells: list[object]) -> bool:
    ne = _nonempty(cells)
    return bool(ne) and all(isinstance(v, str) for v in ne)


def detect_header_rows(grid: SheetGrid, region: Region) -> int:
    height = region.r2 - region.r1 + 1
    h = 0
    for r in range(region.r1, region.r1 + min(MAX_HEADER_ROWS, height)):
        if _all_string(_row_values(grid, r, region.c1, region.c2)):
            h += 1
        else:
            break
    if h == 0:
        return 1  # 首行含非字符串：单行表头兜底
    below_mixed = any(
        not _all_string(_row_values(grid, r, region.c1, region.c2))
        for r in range(region.r1 + h, region.r2 + 1)
    )
    if not below_mixed:
        return 1  # 全表皆字符串：防吞数据区
    return h


def classify_region(grid: SheetGrid, region: Region, header_rows: int) -> SheetRoute:
    height = region.r2 - region.r1 + 1
    width = region.c2 - region.c1 + 1
    data_r1 = region.r1 + header_rows
    if data_r1 > region.r2:
        return SheetRoute.DATA_TABLE  # 只有表头区：按数据表输出（无数据行则产 0 行）

    # 表单式：表头区上方有横向大合并抬头（决算表/签字名单）
    wide_merges = [
        (r1, c1, r2, c2) for r1, c1, r2, c2 in grid.merged
        if region.r1 <= r1 and r2 < data_r1 and (c2 - c1 + 1) >= max(2, width * _FORM_BANNER_RATIO)
    ]
    if wide_merges:
        # 多级分组表头 vs 表单 banner：若宽合并出现在 ≥2 个不同行 → 多级分组 → DATA_TABLE
        # （banner 型表单只有一行宽合并；多级表头每层都有宽合并）
        distinct_rows = {r1 for r1, c1, r2, c2 in wide_merges}
        if len(distinct_rows) < 2:
            return SheetRoute.FORM_SHEET

    # 文档型：≤2 列 + 数据全字符串 + 含长文本（FAQ/政策汇编）
    data_values: list[object] = []
    for r in range(data_r1, region.r2 + 1):
        data_values.extend(_nonempty(_row_values(grid, r, region.c1, region.c2)))
    all_str = bool(data_values) and all(isinstance(v, str) for v in data_values)
    has_long = any(isinstance(v, str) and len(v.strip()) >= _DOC_MIN_LONG for v in data_values)
    if all_str and width <= _DOC_MAX_COLS and has_long:
        return SheetRoute.DOC_SHEET

    return SheetRoute.DATA_TABLE
