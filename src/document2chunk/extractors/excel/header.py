"""表头检测（难点1/3）与 sheet 路线分流（N5/N6，调研报告 §3.6）。

- 表头 = 顶部连续全字符串行（AAAI 类型同质性），上限 5（真实语料最多 3 级）。
- 下方无类型对比（全表皆字符串）时表头封顶 1 行——防吞纯文本数据区（盘点修正③）。
- 分流：文档型（≤2 列全字符串含长文本）、表单式（表头区上方有 ≥80% 宽的横向大合并抬头）。
- 拒升（2026-09-21 grill P1，同日修订）：候选表头行含 URL/纯日期（含裸年份）形态 → 一票否决
  不作表头；≥30 字长句为软信号，须占该行非空格 ≥0.5 才拒升（14 号表头含 2 个长说明列名，
  逐格否决会误杀真表头）。首行即被拒升且区域多于 1 行 → 整区按无表头输出（keys=列N），
  行内容零丢失（19 号 region2/3 教训）；单行区域不拒升（0 产出由兜底救援）。
- 宽合并横幅豁免：整行横跨 ≥半宽的合并抬头（标题横幅）不受拒升——112 定稿
  「标题作为表头路径第一级输出」（Q4），拒它会造成全语料大面积回归。
"""
from __future__ import annotations

import re

from document2chunk.extractors.excel.models import Region, SheetGrid, SheetRoute

MAX_HEADER_ROWS = 5
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_DATEY = re.compile(r"^\d{4}([-/.年]\d{1,2}([-/.月]\d{1,2}日?)?)?年?$")   # 含裸年份「2015」/「2015年」
_SUSPICIOUS_LEN = 30
_SUSPICIOUS_RATIO = 0.5
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


def _wide_merged_row(grid: SheetGrid, region: Region, r: int) -> bool:
    """该行存在横跨 ≥ 半宽（且 ≥2 列）的合并区 → 标题横幅，豁免拒升。"""
    half = max(2, (region.c2 - region.c1 + 1) // 2)
    return any(
        r1 == r2 == r and (c2 - c1 + 1) >= half for r1, c1, r2, c2 in grid.merged
    )


def _row_suspicious(grid: SheetGrid, region: Region, r: int) -> bool:
    """URL/日期形态一票否决；长句须占非空格 ≥_SUSPICIOUS_RATIO（14 号表头含长说明列名，不得误杀）。"""
    if _wide_merged_row(grid, region, r):
        return False
    cells = [
        v
        for v in _row_values(grid, r, region.c1, region.c2)
        if v is not None and not (isinstance(v, str) and v.strip() == "")
    ]
    if not cells:
        return False
    if any(
        isinstance(v, str) and (_URL.search(v) or _DATEY.match(v.strip())) for v in cells
    ):
        return True
    long_n = sum(1 for v in cells if isinstance(v, str) and len(v.strip()) >= _SUSPICIOUS_LEN)
    return long_n / len(cells) >= _SUSPICIOUS_RATIO


def detect_header_rows(grid: SheetGrid, region: Region) -> int:
    height = region.r2 - region.r1 + 1
    width = region.c2 - region.c1 + 1
    # 纯列表：宽 1 且全字符串（如下拉列表源数据）无表头语义，整列皆内容（02 号教训）
    if width == 1 and all(
        _all_string(_row_values(grid, r, region.c1, region.c2))
        for r in range(region.r1, region.r2 + 1)
    ):
        return 0
    h = 0
    for r in range(region.r1, region.r1 + min(MAX_HEADER_ROWS, height)):
        if _all_string(_row_values(grid, r, region.c1, region.c2)) and not _row_suspicious(
            grid, region, r
        ):
            h += 1
        else:
            break
    if h == 0:
        if _row_suspicious(grid, region, region.r1) and height > 1:
            return 0  # 首行拒升（仅多行区域）：整区无表头，行内容全保留
        return 1  # 单行区域不拒升（0 产出由兜底救援）／首行含非字符串：单行表头兜底
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
