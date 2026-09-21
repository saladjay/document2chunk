"""Excel 轨 A 数据模型。坐标全部 0-based 闭区间。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SheetRoute(str, Enum):
    """sheet/区域路线分流（调研报告 §3.6）。"""

    DATA_TABLE = "data_table"
    DOC_SHEET = "doc_sheet"
    FORM_SHEET = "form_sheet"
    EMPTY = "empty"


@dataclass
class CellFmt:
    """openpyxl 二遍读取的格式信息（稀疏存储）。"""

    number_format: str | None = None
    bold: bool = False
    outline_level: int = 0
    error: str | None = None


@dataclass
class SheetGrid:
    """单个 sheet 的实际扫描值域网格 + 格式信息。"""

    name: str
    state: str = "visible"
    values: list[list[object]] = field(default_factory=list)
    formats: dict[tuple[int, int], CellFmt] = field(default_factory=dict)
    merged: list[tuple[int, int, int, int]] = field(default_factory=list)  # (r1,c1,r2,c2)
    hidden_rows: set[int] = field(default_factory=set)
    hidden_cols: set[int] = field(default_factory=set)
    formula_no_cache: set[tuple[int, int]] = field(default_factory=set)
    error_cells: dict[tuple[int, int], str] = field(default_factory=dict)  # 0-based → 错误字面量
    has_external_links: bool = False
    has_pivot: bool = False

    @property
    def n_rows(self) -> int:
        return len(self.values)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.values), default=0)


@dataclass
class Region:
    """subtable 区域，0-based 闭区间。"""

    r1: int
    c1: int
    r2: int
    c2: int


@dataclass
class RowRecord:
    """行级记录：data 键=表头路径，text 喂 embedding，meta 放 flag/溯源。"""

    sheet: str
    row: int  # xlsx 1-based 真实行号
    data: dict[str, object]
    text: str
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class SheetBlock:
    """文档型/表单式 sheet 产出的文本块（Q18/Q19）。"""

    sheet: str
    kind: str  # "doc" | "form"
    text: str
    meta: dict[str, object] = field(default_factory=dict)


@dataclass
class ExcelParseResult:
    file: str
    warnings: list[str] = field(default_factory=list)
    rows: list[RowRecord] = field(default_factory=list)
    blocks: list[SheetBlock] = field(default_factory=list)
    sheets: list[dict] = field(default_factory=list)
