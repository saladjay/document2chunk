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

    # 表单式路线（FORM_SHEET）休眠：112 实测（2026-09-20）真实语料中所有"表单式外观"
    # 的 sheet（评审人员分/经费决算表/边坡巡查）行均为有效记录，且任何"横幅→表单"
    # 几何判据都会把带标题/分组表头的数据表整表聚合成 1 个块（99/230 行丢失，灾难性）。
    # 故不再触发 FORM；带标题的表按标题作为表头路径第一级输出，行级自包含优先（Q4）。
    # Q19 决策偏离已在 112 测试报告中标注，待人工追认。

    # 文档型：≤2 列 + 数据全字符串 + 含长文本（FAQ/政策汇编）
    data_values: list[object] = []
    for r in range(data_r1, region.r2 + 1):
        data_values.extend(_nonempty(_row_values(grid, r, region.c1, region.c2)))
    all_str = bool(data_values) and all(isinstance(v, str) for v in data_values)
    has_long = any(isinstance(v, str) and len(v.strip()) >= _DOC_MIN_LONG for v in data_values)
    if all_str and width <= _DOC_MAX_COLS and has_long:
        return SheetRoute.DOC_SHEET

    return SheetRoute.DATA_TABLE
