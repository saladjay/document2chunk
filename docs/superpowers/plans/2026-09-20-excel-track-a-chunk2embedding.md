# Excel 轨 A（chunk2embedding）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 Excel 类文件解析：xlsx/xlsm/xls/et/csv → 行级 JSON（每行 `{data, text, meta}`），含表头检测、合并下放、值规范化、合计行打标、文档型/表单式分流，暴露 `/parse-excel` 端点。

**Architecture:** 独立 `extractors/excel/` 包（读层 calamine+openpyxl 二遍 → 空行空列切分 → 表头检测与路线分流 → 扁平化+合并下放 → 值规范化 → 合计打标 → envelope 组装）。**不套 `Extractor` Protocol**——该协议产出 Document IR（块结构），与行级 JSON 契约不同；Excel 走独立端点与 serve 函数。老格式（.xls/.et/假扩展名）复用 `format_detect` 指纹 + `legacy_convert` soffice 链路。

**Tech Stack:** python-calamine（值读取）、openpyxl（格式二遍）、FastAPI（既有）、pytest。不引入 pandas。

**规格来源（实现前必读）:**
- `Document2Chunk-Excel解析技术调研报告.md`（双轨架构、§4 schema、§3.6 路线分流）
- `Document2Chunk-Excel难点定档表.md`（28 项定档、验收标准）
- `Excel语料盘点-draft.md`（验收语料与逐文件理由）

## Global Constraints

- Python `>=3.10`；核心依赖保持仅 `pydantic`——新依赖只进 `excel` extra：`python-calamine>=0.7.0`、`openpyxl>=3.1.0`
- 键规则（定稿 Q7）：表头路径 `-` 连接；表头内空白压单空格；撞名加`（2）`；空表头`列N`（N=1-based 列号）
- 值规则（定稿 Q6/Q14）：显示值规范化——千分位不产出、货币符保留、百分比保留 `%`、日期 ISO、字符串 trim、空值→`None`
- 分层原则（定稿 Q4）：溯源/flag 只进 `meta`，不进 `data`、不进 `text`；`text` = `"键: 值; "` 平铺，null 键值对不进 text
- 合并单元格值下放每行，不加溯源标记
- 合计行：轨 A 保留 + `meta.from_total` 打标
- 隐藏 sheet 跳过（记 warning）；隐藏行/列解析 + sheet 级打标
- envelope（定稿 Q9 + Q18/Q19 扩展）：`{file, warnings, rows[], blocks[], sheets[]}`
- 坐标约定：代码内部全 0-based 闭区间；输出 `row` 字段用 xlsx 1-based 真实行号
- 测试命名：新失败测试用 `test_red_*` 前缀（仓库惯例）；命令 `uv run -m pytest ...`
- commit 格式 `type(scope): 中文描述——说明`，结尾加 `Co-Authored-By: Claude <noreply@anthropic.com>`
- 若 `format_detect.identify` 返回的 `Detection` 字段名与计划假设不符，以 `src/document2chunk/format_detect.py:147-156` 实际代码为准（只读适配，不改其签名）

---

### Task 1: excel extra 依赖 + 数据模型 + 异常 + 测试夹具工厂

**Files:**
- Modify: `pyproject.toml`（extras 增加 `excel`）
- Modify: `src/document2chunk/exceptions.py`（新增 `ExcelParseError`）
- Create: `src/document2chunk/extractors/excel/__init__.py`
- Create: `src/document2chunk/extractors/excel/models.py`
- Create: `tests/_excel_fixtures.py`
- Test: `tests/test_excel_models.py`

**Interfaces:**
- Produces: `ExcelParseError(Document2ChunkError)`（既有 handler 映射 422）
- Produces: `SheetRoute(str, Enum)`：`DATA_TABLE / DOC_SHEET / FORM_SHEET / EMPTY`
- Produces: `CellFmt(number_format: str|None, bold: bool, outline_level: int)`
- Produces: `SheetGrid(name, state, values, formats, merged, hidden_rows, hidden_cols, formula_no_cache)`，属性 `n_rows/n_cols`
- Produces: `Region(r1, c1, r2, c2)`、`RowRecord(sheet, row, data, text, meta)`、`SheetBlock(sheet, kind, text, meta)`、`ExcelParseResult(file, warnings, rows, blocks, sheets)`
- Produces: `tests/_excel_fixtures.build_xlsx(sheets, *, merges, bolds, number_formats, hidden_cols, hidden_rows, hidden_sheets, col_widths_ignore) -> bytes`

- [ ] **Step 1: 安装依赖并验证可导入**

```bash
cd /d/github/document2chunk
uv pip install -e ".[excel,dev,legacy]"
uv run python -c "from python_calamine import load_workbook; import openpyxl; print('ok')"
```

Expected: `ok`。`pyproject.toml` 的 `[project]` extras（:17-24 附近）追加一行：

```toml
excel = ["python-calamine>=0.7.0", "openpyxl>=3.1.0"]
```

- [ ] **Step 2: 写失败测试（模型与夹具工厂）**

创建 `tests/test_excel_models.py`：

```python
"""Excel 轨 A：数据模型与夹具工厂。"""


def test_red_models_importable():
    from document2chunk.extractors.excel.models import (
        CellFmt, ExcelParseResult, Region, RowRecord, SheetBlock, SheetGrid, SheetRoute,
    )

    g = SheetGrid(name="s")
    assert g.n_rows == 0 and g.n_cols == 0
    assert SheetRoute.DATA_TABLE == "data_table"


def test_red_build_xlsx_fixture_roundtrip():
    from python_calamine import load_workbook
    import io

    from tests._excel_fixtures import build_xlsx

    data = build_xlsx({"Sheet1": [["名称", "数量"], ["甲", 3]]})
    wb = load_workbook(io.BytesIO(data))
    rows = wb.get_sheet_by_name("Sheet1").to_python(skip_empty_area=True)
    assert rows == [["名称", "数量"], ["甲", 3]]


def test_red_excel_parse_error_is_document_error():
    from document2chunk.exceptions import Document2ChunkError, ExcelParseError

    assert issubclass(ExcelParseError, Document2ChunkError)
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_models.py -v
```

Expected: 3 FAIL（`ModuleNotFoundError: document2chunk.extractors.excel` 等）。

- [ ] **Step 4: 实现模型、异常、夹具工厂**

`src/document2chunk/extractors/excel/__init__.py`：

```python
"""Excel 解析（轨 A: chunk2embedding）。"""
```

`src/document2chunk/extractors/excel/models.py`：

```python
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
```

`src/document2chunk/exceptions.py` 末尾追加：

```python
class ExcelParseError(Document2ChunkError):
    """Excel 解析失败（422）。"""
```

（若 `exceptions.py` 中 `Document2ChunkError` 定义名不同，以实际为准——探索报告确认其存在并映射 422。）

`tests/_excel_fixtures.py`：

```python
"""Excel 测试夹具：运行时用 openpyxl 生成 xlsx 字节（对齐 tests/_fixtures.py 的 PDF 夹具模式）。"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font


def build_xlsx(
    sheets: dict[str, list[list]],
    *,
    merges: tuple[str, ...] = (),          # 如 "A1:B1"
    bolds: dict[tuple[int, int], bool] = None,   # (row, col) 0-based
    number_formats: dict[tuple[int, int], str] = None,  # (row, col) 0-based
    hidden_cols: tuple[int, ...] = (),     # 0-based
    hidden_rows: tuple[int, ...] = (),     # 0-based
    hidden_sheets: tuple[str, ...] = (),
) -> bytes:
    bolds = bolds or {}
    number_formats = number_formats or {}
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                cell = ws.cell(row=r + 1, column=c + 1)
                if isinstance(v, str) and v.startswith("="):
                    cell.value = v  # 公式：openpyxl 不写缓存值 → 可测"无缓存"场景
                else:
                    cell.value = v
                if (r, c) in bolds:
                    cell.font = Font(bold=bolds[(r, c)])
                if (r, c) in number_formats:
                    cell.number_format = number_formats[(r, c)]
        for m in merges:
            ws.merge_cells(m)
        for c in hidden_cols:
            ws.column_dimensions[ws.cell(row=1, column=c + 1).column_letter].hidden = True
        for r in hidden_rows:
            ws.row_dimensions[r + 1].hidden = True
    for name in hidden_sheets:
        wb[name].sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_models.py -v
```

Expected: 3 PASS。

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/document2chunk/exceptions.py src/document2chunk/extractors/excel/ tests/_excel_fixtures.py tests/test_excel_models.py
git commit -m "feat(excel): excel extra 依赖+轨A数据模型+夹具工厂——E-1 底座

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 读取层 reader.py（calamine 值 + openpyxl 格式二遍）

**Files:**
- Create: `src/document2chunk/extractors/excel/reader.py`
- Test: `tests/test_excel_reader.py`

**Interfaces:**
- Consumes: `SheetGrid/CellFmt`（Task 1）、`build_xlsx`（Task 1）
- Produces: `read_sheet_grids(data: bytes) -> tuple[list[SheetGrid], list[str]]`——隐藏 sheet 跳过并给 warning；外部链接/透视表给 warning；公式无缓存坐标进 `grid.formula_no_cache`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_reader.py`：

```python
"""读取层：calamine 值域 + openpyxl 格式二遍。"""
from tests._excel_fixtures import build_xlsx


def test_red_reader_basic_grid():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx({"表一": [["名称", "数量"], ["甲", 3]]})
    grids, warnings = read_sheet_grids(data)
    assert warnings == []
    assert len(grids) == 1
    g = grids[0]
    assert g.name == "表一"
    assert g.values == [["名称", "数量"], ["甲", 3]]


def test_red_reader_hidden_sheet_skipped():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx(
        {"可见": [["a", 1]], "暗账": [["b", 2]]},
        hidden_sheets=("暗账",),
    )
    grids, warnings = read_sheet_grids(data)
    assert [g.name for g in grids] == ["可见"]
    assert any("暗账" in w for w in warnings)


def test_red_reader_merged_and_formats():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx(
        {"s": [["部门", None, "值"], ["华东", None, 1.5], [None, None, 2.5]]},
        merges=("A2:A3",),
        bolds={(0, 0): True},
        number_formats={(1, 2): '"¥"#,##0.00'},
    )
    grids, _ = read_sheet_grids(data)
    g = grids[0]
    assert g.merged == [(1, 0, 2, 0)]          # A2:A3 → 0-based (1,0)-(2,0)
    assert g.formats[(0, 0)].bold is True
    assert "¥" in g.formats[(1, 2)].number_format


def test_red_reader_formula_no_cache_detected():
    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx({"s": [["a", 1], ["b", "=SUM(B1:B1)"]]})
    grids, warnings = read_sheet_grids(data)
    g = grids[0]
    assert (1, 1) in g.formula_no_cache        # openpyxl 写的公式无缓存值
    assert any("无缓存" in w for w in warnings)


def test_red_reader_external_link_warning():
    import zipfile, io

    from document2chunk.extractors.excel.reader import read_sheet_grids

    data = build_xlsx({"s": [["a", 1]]})
    # 在 zip 中补一个 externalLinks 条目以触发检测
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for item in src.namelist():
            out.writestr(item, src.read(item))
        out.writestr("xl/externalLinks/externalLink1.xml", "<xml/>")
    grids, warnings = read_sheet_grids(buf.getvalue())
    assert any("外部" in w for w in warnings)
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_reader.py -v
```

Expected: 5 FAIL（`reader` 模块不存在）。

- [ ] **Step 3: 实现 reader.py**

创建 `src/document2chunk/extractors/excel/reader.py`：

```python
"""读取层：python-calamine 读值（快）+ openpyxl 二遍读格式（准）。

- calamine `skip_empty_area=True` 天然给出"实际扫描值域"（定档 #5：不信声明 dimension）。
- openpyxl 二遍只取：合并 ranges、数字格式、加粗、outline、隐藏行列、无缓存公式坐标。
- 单元格数超 `_OPENPYXL_CELL_LIMIT` 时跳过二遍（性能护栏，定档 #18），并记 warning。
"""
from __future__ import annotations

import io
import zipfile

from document2chunk.extractors.excel.models import CellFmt, SheetGrid

_OPENPYXL_CELL_LIMIT = 200_000


def _zip_flags(data: bytes) -> dict[str, bool]:
    flags = {"external_links": False, "pivot": False}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for n in zf.namelist():
                low = n.lower()
                if low.startswith("xl/externalinks/"):
                    flags["external_links"] = True
                elif low.startswith("xl/pivottables/") or low.startswith("xl/pivotcache/"):
                    flags["pivot"] = True
    except zipfile.BadZipFile:
        pass
    return flags


def _read_openpyxl_info(data: bytes, sheet_names: list[str]) -> dict[str, dict]:
    from openpyxl import load_workbook as _owlb

    wb_v = _owlb(io.BytesIO(data), data_only=True)   # 值/格式
    wb_f = _owlb(io.BytesIO(data), data_only=False)  # 公式坐标（data_only=True 时无缓存公式读不到 'f'）
    out: dict[str, dict] = {}
    for name in sheet_names:
        ws = wb_v[name]
        merged = [
            (rng.min_row - 1, rng.min_col - 1, rng.max_row - 1, rng.max_col - 1)
            for rng in ws.merged_cells.ranges
        ]
        formats: dict[tuple[int, int], CellFmt] = {}
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                r, c = cell.row - 1, cell.column - 1
                fmt = cell.number_format or "General"
                bold = bool(cell.font and cell.font.bold)
                outline = int(getattr(cell, "outline_level", 0) or 0)
                if fmt != "General" or bold or outline:
                    formats[(r, c)] = CellFmt(number_format=fmt, bold=bold, outline_level=outline)
        formula_no_cache: set[tuple[int, int]] = set()
        ws_f = wb_f[name]
        for row in ws_f.iter_rows():
            for cell in row:
                if cell.data_type == "f" and cell.row <= ws.max_row and cell.column <= ws.max_column:
                    v = ws.cell(row=cell.row, column=cell.column).value
                    if v in (None, ""):
                        formula_no_cache.add((cell.row - 1, cell.column - 1))
        hidden_rows = {i - 1 for i, dim in ws.row_dimensions.items() if dim.hidden}
        hidden_cols = {i - 1 for i, dim in ws.column_dimensions.items() if dim.hidden}
        out[name] = {
            "state": ws.sheet_state,
            "merged": merged,
            "formats": formats,
            "hidden_rows": hidden_rows,
            "hidden_cols": hidden_cols,
            "formula_no_cache": formula_no_cache,
        }
    return out


def read_sheet_grids(data: bytes) -> tuple[list[SheetGrid], list[str]]:
    """返回（可见 sheet 网格, warnings）。"""
    from python_calamine import load_workbook  # 延迟导入：excel extra

    warnings: list[str] = []
    flags = _zip_flags(data)
    if flags["external_links"]:
        warnings.append("文件含外部工作簿链接，相关单元格为保存时的缓存值")
    if flags["pivot"]:
        warnings.append("文件含数据透视表，其数据可能不在单元格中")

    wb = load_workbook(io.BytesIO(data))
    names = list(wb.sheet_names)
    sheet_rows = [wb.get_sheet_by_name(n).to_python(skip_empty_area=True) for n in names]
    total_cells = sum(len(r) for rows in sheet_rows for r in rows)

    info: dict[str, dict] = {}
    if total_cells <= _OPENPYXL_CELL_LIMIT:
        info = _read_openpyxl_info(data, names)
    else:
        warnings.append(
            f"单元格数 {total_cells} 超过上限 {_OPENPYXL_CELL_LIMIT}，跳过格式二遍（无合并/格式/隐藏信息）"
        )
    no_cache_total = sum(len(d.get("formula_no_cache", set())) for d in info.values())
    if no_cache_total:
        warnings.append(
            f"{no_cache_total} 个公式单元格无缓存值（文件可能由程序生成），读为空值"
        )

    grids: list[SheetGrid] = []
    for i, name in enumerate(names):
        d = info.get(name, {})
        if d.get("state", "visible") != "visible":
            warnings.append(f"隐藏 sheet「{name}」已跳过")
            continue
        grids.append(
            SheetGrid(
                name=name,
                values=[list(r) for r in sheet_rows[i]],
                merged=d.get("merged", []),
                formats=d.get("formats", {}),
                hidden_rows=d.get("hidden_rows", set()),
                hidden_cols=d.get("hidden_cols", set()),
                formula_no_cache=d.get("formula_no_cache", set()),
                has_external_links=flags["external_links"],
                has_pivot=flags["pivot"],
            )
        )
    return grids, warnings
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_reader.py tests/test_excel_models.py -v
```

Expected: 8 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/reader.py tests/test_excel_reader.py
git commit -m "feat(excel): 读取层——calamine 实际值域+openpyxl 格式二遍——难点5/6/7/19

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 表边界 boundary.py（空行横切 + 空列纵切）

**Files:**
- Create: `src/document2chunk/extractors/excel/boundary.py`
- Test: `tests/test_excel_boundary.py`

**Interfaces:**
- Consumes: `SheetGrid`、`read_sheet_grids`、`build_xlsx`
- Produces: `split_regions(grid: SheetGrid) -> list[Region]`——空行切横带、带内空列切段（难点2：堆叠表+并排表）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_boundary.py`：

```python
"""表边界：空行空列切分。"""
from tests._excel_fixtures import build_xlsx

_H = [["名称", "数量"]]


def _grid(data_rows, name="s"):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({name: [_H[0]] + data_rows}))
    return grids[0]


def test_red_single_table_one_region():
    from document2chunk.extractors.excel.boundary import split_regions

    g = _grid([["甲", 1], ["乙", 2]])
    regions = split_regions(g)
    assert len(regions) == 1
    assert (regions[0].r1, regions[0].c1, regions[0].r2, regions[0].c2) == (0, 0, 2, 1)


def test_red_stacked_tables_split_by_blank_row():
    from document2chunk.extractors.excel.boundary import split_regions

    # 网格 = 表头行 + 3 数据行：r0 名称 / r1 甲 / r2 空 / r3 乙
    g = _grid([["甲", 1], [None, None], ["乙", 2]])
    regions = split_regions(g)
    assert len(regions) == 2
    assert regions[0].r1 == 0 and regions[0].r2 == 1
    assert regions[1].r1 == 3 and regions[1].r2 == 3


def test_red_side_by_side_tables_split_by_blank_col():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(
        build_xlsx({"s": [["名称", "数量", None, "姓名"], ["甲", 1, None, "张三"]]})
    )
    regions = split_regions(grids[0])
    assert len(regions) == 2
    assert regions[0].c2 == 1
    assert regions[1].c1 == 3


def test_red_empty_grid_no_regions():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.models import SheetGrid

    assert split_regions(SheetGrid(name="empty")) == []


def test_red_whitespace_only_cell_is_blank():
    from document2chunk.extractors.excel.boundary import split_regions

    g = _grid([["甲", 1], ["  ", None], ["乙", 2]])
    assert len(split_regions(g)) == 2
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_boundary.py -v
```

Expected: 5 FAIL。

- [ ] **Step 3: 实现 boundary.py**

```python
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
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_boundary.py -v
```

Expected: 5 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/boundary.py tests/test_excel_boundary.py
git commit -m "feat(excel): 表边界——空行横切+空列纵切 subtable——难点2

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 表头检测与路线分流 header.py

**Files:**
- Create: `src/document2chunk/extractors/excel/header.py`
- Test: `tests/test_excel_header.py`

**Interfaces:**
- Consumes: `SheetGrid/Region/SheetRoute`
- Produces: `detect_header_rows(grid, region) -> int`（1..5）
- Produces: `classify_region(grid, region, header_rows) -> SheetRoute`（难点3 + N5/N6 分流）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_header.py`：

```python
"""表头检测与路线分流。"""
from tests._excel_fixtures import build_xlsx


def _grid(rows, merges=()):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}, merges=merges))
    return grids[0]


def _full(grid):
    from document2chunk.extractors.excel.boundary import split_regions

    regions = split_regions(grid)
    assert len(regions) == 1
    return regions[0]


def test_red_single_row_header():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    g = _grid([["名称", "数量"], ["甲", 1], ["乙", 2]])
    region = _full(g)
    h = detect_header_rows(g, region)
    assert h == 1
    assert classify_region(g, region, h) is SheetRoute.DATA_TABLE


def test_red_two_row_header_with_type_contrast():
    from document2chunk.extractors.excel.header import detect_header_rows

    # 双行表头：第二行也是字符串，但数据行有数字（类型对比）→ 延伸到 2
    g = _grid([["华东", None], ["子列1", "子列2"], ["x", 1]])
    region = _full(g)
    assert detect_header_rows(g, region) == 2


def test_red_pure_text_sheet_not_swallowed():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    # FAQ 型：2 列全字符串长文本 → 只认 1 行表头 + 路线=文档型（盘点修正③ + N5）
    long_q = "为什么上传后没有反应？请检查网络后重试，若仍未解决请联系管理员处理。"
    g = _grid([["问题", "答案"], [long_q, "重启服务即可解决，若仍失败请联系值班同学确认配置。"]])
    region = _full(g)
    h = detect_header_rows(g, region)
    assert h == 1
    assert classify_region(g, region, h) is SheetRoute.DOC_SHEET


def test_red_all_string_many_cols_stays_data_table():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    # 通讯录对照（组织页面数据）：>2 列全字符串 → 数据表，不得误判文档型/多行表头
    g = _grid([["姓名", "部门", "职务"], ["张三", "研发中心", "工程师"], ["李四", "综合部", "经理"]])
    region = _full(g)
    h = detect_header_rows(g, region)
    assert h == 1
    assert classify_region(g, region, h) is SheetRoute.DATA_TABLE


def test_red_form_sheet_by_wide_banner_merge():
    from document2chunk.extractors.excel.header import classify_region, detect_header_rows
    from document2chunk.extractors.excel.models import SheetRoute

    # 决算表型：顶部横向大合并抬头 + 表头 + 数据
    g = _grid(
        [
            ["2024年度研发经费决算表", None, None],
            ["科目", "金额", "备注"],
            ["人员费", 100, None],
            ["设备费", 200, None],
        ],
        merges=("A1:C1",),
    )
    region = _full(g)
    h = detect_header_rows(g, region)
    assert classify_region(g, region, h) is SheetRoute.FORM_SHEET


def test_red_header_cap_at_five():
    from document2chunk.extractors.excel.header import detect_header_rows

    rows = [[f"h{r}", "文本"] for r in range(8)]
    rows.append(["x", 1])
    g = _grid(rows)
    assert detect_header_rows(g, _full(g)) == 5
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_header.py -v
```

Expected: 6 FAIL。

- [ ] **Step 3: 实现 header.py**

```python
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
_FORM_BANNER_RATIO = 0.8


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
    for r1, c1, r2, c2 in grid.merged:
        if region.r1 <= r1 and r2 < data_r1 and (c2 - c1 + 1) >= max(2, width * _FORM_BANNER_RATIO):
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
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_header.py -v
```

Expected: 6 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/header.py tests/test_excel_header.py
git commit -m "feat(excel): 表头检测+路线分流——类型同质性+防吞数据区+文档型/表单式

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 扁平化与合并下放 flatten.py

**Files:**
- Create: `src/document2chunk/extractors/excel/flatten.py`
- Test: `tests/test_excel_flatten.py`

**Interfaces:**
- Consumes: `SheetGrid/Region`
- Produces: `build_merge_origin(grid) -> dict[tuple[int,int], tuple[int,int]]`（cell→合并区左上角；非阴影格不入场）
- Produces: `flatten_header(grid, region, header_rows) -> dict[int, str]`（列号→键；`-` 连接、`列N`、`（2）`撞名）
- Produces: `fill_region_values(grid, region, header_rows) -> list[dict[int, object]]`（数据区逐行，合并值下放，空值为 None 不入场）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_flatten.py`：

```python
"""扁平化与合并下放。"""
from tests._excel_fixtures import build_xlsx


def _grid(rows, merges=()):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}, merges=merges))
    return grids[0]


def test_red_flatten_single_header():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    g = _grid([["名称", "数量"], ["甲", 1]])
    keys = flatten_header(g, split_regions(g)[0], 1)
    assert keys == {0: "名称", 1: "数量"}


def test_red_flatten_multilevel_with_merge():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    # 华东 合并 A1:A2，子列1/子列2 在第二行 → "华东-子列1" / "华东-子列2"
    g = _grid(
        [
            ["华东", None, "华南"],
            [None, None, None],
            ["子列1", "子列2", "子列1"],
            ["x", 1, 2],
        ],
        merges=("A1:B2",),
    )
    keys = flatten_header(g, split_regions(g)[0], 2)
    assert keys[0] == "华东-子列1"
    assert keys[1] == "华东-子列2"
    assert keys[2] == "华南-子列1"


def test_red_flatten_duplicate_and_empty_headers():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    g = _grid([["名称", None, "名称"], ["a", 1, "b"]])
    keys = flatten_header(g, split_regions(g)[0], 1)
    assert keys[0] == "名称"
    assert keys[1] == "列2"
    assert keys[2] == "名称（2）"


def test_red_flatten_header_newline_squeezed():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import flatten_header

    g = _grid([["科目\n名称", "金额（元）\n2024"], ["人员费", 100]])
    keys = flatten_header(g, split_regions(g)[0], 1)
    assert keys[0] == "科目 名称"
    assert keys[1] == "金额（元） 2024"


def test_red_merge_value_forward_filled():
    from document2chunk.extractors.excel.boundary import split_regions
    from document2chunk.extractors.excel.flatten import fill_region_values

    # 「路段」纵向合并：江罗高速 对 3 行设备（难点1 经典场景）
    g = _grid(
        [
            ["路段", "设备"],
            ["江罗高速", None],
            [None, "摄像头"],
            [None, "传感器"],
        ],
        merges=("A2:A4",),
    )
    region = split_regions(g)[0]
    recs = fill_region_values(g, region, 1)
    assert recs[0] == {0: "江罗高速"}
    assert recs[1] == {0: "江罗高速", 1: "摄像头"}
    assert recs[2] == {0: "江罗高速", 1: "传感器"}
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_flatten.py -v
```

Expected: 5 FAIL。

- [ ] **Step 3: 实现 flatten.py**

```python
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


def _cell_value(grid: SheetGrid, origin: dict, r: int, c: int) -> object:
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
            v = _cell_value(grid, origin, r, c)
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
            v = _cell_value(grid, origin, r, c)
            if v is not None and not (isinstance(v, str) and v.strip() == ""):
                rec[c] = v
        out.append(rec)
    return out
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_flatten.py -v
```

Expected: 5 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/flatten.py tests/test_excel_flatten.py
git commit -m "feat(excel): 表头扁平化+合并值下放——华东-子列1 键形+Q4 下放

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 值规范化 values.py

**Files:**
- Create: `src/document2chunk/extractors/excel/values.py`
- Test: `tests/test_excel_values.py`

**Interfaces:**
- Consumes: `CellFmt`
- Produces: `normalize_value(value: object, fmt: CellFmt | None) -> object`——JSON 就绪的单值（Q6/Q14 规则）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_values.py`：

```python
"""值规范化：显示值 − 千分位 + ISO 日期 + trim；货币符/百分号保留。"""
from datetime import date, datetime

from document2chunk.extractors.excel.models import CellFmt
from document2chunk.extractors.excel.values import normalize_value as nv


def test_red_none_and_blank_to_none():
    assert nv(None, None) is None
    assert nv("", None) is None
    assert nv("   ", None) is None


def test_red_string_trimmed():
    assert nv("  甲  ", None) == "甲"


def test_red_plain_number_unchanged():
    assert nv(1024.5, None) == 1024.5
    assert nv(3, None) == 3
    assert nv(3.0, None) == 3          # float 整数收敛为 int


def test_red_currency_kept_thousands_not_produced():
    fmt = CellFmt(number_format='"¥"#,##0.00')
    assert nv(1024.5, fmt) == "¥1024.50"


def test_red_percent_semantics():
    assert nv(0.12, CellFmt(number_format="0%")) == "12%"
    assert nv(0.126, CellFmt(number_format="0.00%")) == "12.6%"


def test_red_dates_iso():
    assert nv(datetime(2023, 12, 1, 0, 0, 0), None) == "2023-12-01"
    assert nv(datetime(2023, 12, 1, 8, 30, 0), None) == "2023-12-01T08:30:00"
    assert nv(date(2023, 12, 1), None) == "2023-12-01"


def test_red_bool_passthrough():
    assert nv(True, None) is True
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_values.py -v
```

Expected: 7 FAIL。

- [ ] **Step 3: 实现 values.py**

```python
"""值规范化（Q6/Q14 定稿）：显示值 − 千分位 + ISO 日期 + trim；货币符/百分号保留。"""
from __future__ import annotations

import re
from datetime import date, datetime

from document2chunk.extractors.excel.models import CellFmt

_DEC = re.compile(r"0\.(0+)")
_CUR = re.compile(r"^[^#0,\-\+\s%eE]+")


def _currency_symbol(fmt: str) -> str:
    fmt = (fmt or "").replace('"', "")  # 去引号（Excel 数字格式中货币符常写作 "¥"）
    m = _CUR.match(fmt)
    if not m:
        return ""
    sym = m.group(0).replace("_", "").strip()
    return "" if sym.lower() == "general" else sym


def _decimal_places(fmt: str) -> int:
    m = _DEC.search(fmt)
    return len(m.group(1)) if m else 0


def _percent(value: float, places: int) -> str:
    s = f"{value * 100:.{places}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return f"{s}%"


def normalize_value(value: object, fmt: CellFmt | None = None) -> object:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        if value.hour or value.minute or value.second or value.microsecond:
            return value.isoformat(sep="T")
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        nf = (fmt.number_format if fmt and fmt.number_format else "") or "General"
        if "%" in nf:
            return _percent(float(value), _decimal_places(nf))
        sym = _currency_symbol(nf)
        if sym:
            return f"{sym}{float(value):.{_decimal_places(nf)}f}"
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    return str(value)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_values.py -v
```

Expected: 7 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/values.py tests/test_excel_values.py
git commit -m "feat(excel): 值规范化——货币符/百分号保留+ISO 日期+空值归一

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 合计行打标 totals.py

**Files:**
- Create: `src/document2chunk/extractors/excel/totals.py`
- Test: `tests/test_excel_totals.py`

**Interfaces:**
- Consumes: `SheetGrid/Region`
- Produces: `mark_total_rows(grid, region, header_rows) -> dict[int, str]`（0-based 数据行绝对行号 → `"keyword" | "checksum"`；轨 A 只打标，轨 B 二期用同一函数剔除）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_totals.py`：

```python
"""合计行打标：行首词表 + 校验和（难点13/N6）。"""
from tests._excel_fixtures import build_xlsx


def _grid(rows):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}))
    return grids[0]


def _region(grid):
    from document2chunk.extractors.excel.boundary import split_regions

    return split_regions(grid)[0]


def test_red_keyword_total_row():
    from document2chunk.extractors.excel.totals import mark_total_rows

    g = _grid([["科目", "金额"], ["人员费", 100], ["设备费", 200], ["合计", 300]])
    marks = mark_total_rows(g, _region(g), 1)
    assert marks == {3: "keyword"}


def test_red_checksum_total_row_without_label():
    from document2chunk.extractors.excel.totals import mark_total_rows

    g = _grid([["科目", "金额"], ["人员费", 100], ["设备费", 200], [None, 300]])
    marks = mark_total_rows(g, _region(g), 1)
    assert marks == {3: "checksum"}


def test_red_normal_row_not_marked():
    from document2chunk.extractors.excel.totals import mark_total_rows

    g = _grid([["科目", "金额"], ["人员费", 100], ["设备费", 200], ["材料费", 50]])
    assert mark_total_rows(g, _region(g), 1) == {}


def test_red_column_named_total_not_misleading():
    from document2chunk.extractors.excel.totals import mark_total_rows

    # 词表只匹配行首列：行首是科目名、首列表头恰含"合计金额"的数据行不得误杀
    g = _grid([["科目", "合计金额"], ["人员费", 100], ["设备费", 200]])
    assert mark_total_rows(g, _region(g), 1) == {}
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_totals.py -v
```

Expected: 4 FAIL。

- [ ] **Step 3: 实现 totals.py**

```python
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
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_totals.py -v
```

Expected: 4 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/totals.py tests/test_excel_totals.py
git commit -m "feat(excel): 合计行打标——行首词表+校验和，词表只匹配行首列防误杀

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: 解析编排 parser.py + envelope 组装 serializer.py

**Files:**
- Create: `src/document2chunk/extractors/excel/serializer.py`
- Create: `src/document2chunk/extractors/excel/parser.py`
- Test: `tests/test_excel_parser.py`

**Interfaces:**
- Consumes: Task 2–7 全部模块
- Produces: `row_text(data: dict) -> str`（`"k: v; "` 平铺，null 不进）
- Produces: `to_envelope(result: ExcelParseResult) -> dict`（`{file, warnings, rows, blocks, sheets}`）
- Produces: `parse_excel_bytes(data: bytes, name: str = "") -> ExcelParseResult`——毒输入一律转 `ExcelParseError`（不 500，对齐 MuPDF 事故教训）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_parser.py`：

```python
"""解析编排与 envelope。"""
from tests._excel_fixtures import build_xlsx


def test_red_full_pipeline_mixed_workbook():
    from document2chunk.extractors.excel.parser import parse_excel_bytes
    from document2chunk.extractors.excel.serializer import to_envelope

    data = build_xlsx(
        {
            "明细": [["名称", "数量"], ["甲", 1], ["乙", 2], ["合计", 3]],
            "FAQ": [
                ["问题", "答案"],
                ["为什么上传后没有反应？请检查网络连接是否正常，或稍后重试。", "已处理完成，若仍未生效请联系管理员核实账号权限配置。"],
            ],
        }
    )
    result = parse_excel_bytes(data, "t.xlsx")
    env = to_envelope(result)
    assert env["file"] == "t.xlsx"
    assert [r["data"] for r in env["rows"][:2]] == [
        {"名称": "甲", "数量": 1},
        {"名称": "乙", "数量": 2},
    ]
    assert env["rows"][0]["text"] == "名称: 甲; 数量: 1"
    totals = [r for r in env["rows"] if r["meta"].get("from_total")]
    assert len(totals) == 1 and totals[0]["data"]["名称"] == "合计"
    assert env["blocks"] and env["blocks"][0]["kind"] == "doc"
    routes = {s["sheet"]: s["routes"] for s in env["sheets"]}
    assert routes["明细"] == ["data_table"]
    assert routes["FAQ"] == ["doc_sheet"]


def test_red_empty_sheet_warning_and_summary():
    from document2chunk.extractors.excel.parser import parse_excel_bytes

    result = parse_excel_bytes(build_xlsx({"空表": [[None]]}), "t.xlsx")
    assert result.rows == []
    assert any("空" in w for w in result.warnings)
    assert result.sheets[0]["routes"] == ["empty"]


def test_red_hidden_row_flagged_but_present():
    from document2chunk.extractors.excel.parser import parse_excel_bytes

    result = parse_excel_bytes(
        build_xlsx({"s": [["名称", "数量"], ["甲", 1], ["乙", 2]]}, hidden_rows=(1,))
    )
    rows = {r.data["名称"]: r for r in result.rows}
    assert rows["甲"].meta["hidden"] is True
    assert rows["乙"].meta["hidden"] is False
    assert rows["甲"].row == 2 and rows["乙"].row == 3   # 1-based 真实行号


def test_red_poison_input_raises_excel_parse_error():
    import pytest

    from document2chunk.exceptions import ExcelParseError
    from document2chunk.extractors.excel.parser import parse_excel_bytes

    with pytest.raises(ExcelParseError):
        parse_excel_bytes(b"PK\x03\x04 not really a zip", "bad.xlsx")
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_parser.py -v
```

Expected: 4 FAIL（`parser`/`serializer` 模块不存在）。

- [ ] **Step 3: 实现 serializer.py 与 parser.py**

`src/document2chunk/extractors/excel/serializer.py`：

```python
"""行级 JSON envelope 组装（Q8/Q9 + Q18/Q19 扩展）。"""
from __future__ import annotations

from document2chunk.extractors.excel.models import ExcelParseResult


def row_text(data: dict[str, object]) -> str:
    """`"键: 值; "` 平铺；值为 None 的键值对不进 text。"""
    return "; ".join(f"{k}: {v}" for k, v in data.items() if v is not None)


def to_envelope(result: ExcelParseResult) -> dict:
    return {
        "file": result.file,
        "warnings": result.warnings,
        "rows": [
            {"sheet": r.sheet, "row": r.row, "data": r.data, "text": r.text, "meta": r.meta}
            for r in result.rows
        ],
        "blocks": [
            {"sheet": b.sheet, "kind": b.kind, "text": b.text, "meta": b.meta}
            for b in result.blocks
        ],
        "sheets": result.sheets,
    }
```

`src/document2chunk/extractors/excel/parser.py`：

```python
"""解析编排：xlsx bytes → ExcelParseResult（轨 A: chunk2embedding）。"""
from __future__ import annotations

from document2chunk.exceptions import ExcelParseError
from document2chunk.extractors.excel.boundary import split_regions
from document2chunk.extractors.excel.flatten import fill_region_values, flatten_header
from document2chunk.extractors.excel.header import classify_region, detect_header_rows
from document2chunk.extractors.excel.models import (
    ExcelParseResult,
    Region,
    RowRecord,
    SheetBlock,
    SheetGrid,
    SheetRoute,
)
from document2chunk.extractors.excel.reader import read_sheet_grids
from document2chunk.extractors.excel.serializer import row_text
from document2chunk.extractors.excel.totals import mark_total_rows
from document2chunk.extractors.excel.values import normalize_value


def _is_blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _doc_block_for_row(grid: SheetGrid, r: int, c1: int, c2: int) -> SheetBlock | None:
    row = grid.values[r] if r < grid.n_rows else []
    vals = [str(v).strip() for v in row[c1 : c2 + 1] if not _is_blank(v)]
    if not vals:
        return None
    text = f"{vals[0]}: {vals[1]}" if len(vals) >= 2 else vals[0]
    return SheetBlock(sheet=grid.name, kind="doc", text=text, meta={"row": r + 1})


def _form_block(grid: SheetGrid, region: Region) -> SheetBlock:
    lines: list[str] = []
    for r in range(region.r1, region.r2 + 1):
        row = grid.values[r] if r < grid.n_rows else []
        cells = [str(v).strip() for v in row[region.c1 : region.c2 + 1] if not _is_blank(v)]
        if cells:
            lines.append("; ".join(cells))
    return SheetBlock(
        sheet=grid.name,
        kind="form",
        text="\n".join(lines),
        meta={"rows": f"{region.r1 + 1}-{region.r2 + 1}"},
    )


def parse_excel_bytes(data: bytes, name: str = "") -> ExcelParseResult:
    try:
        grids, warnings = read_sheet_grids(data)
    except ExcelParseError:
        raise
    except Exception as exc:  # 毒输入一律转 ExcelParseError（422），绝不 500
        raise ExcelParseError(f"{name or 'excel'}: 无法解析（{type(exc).__name__}: {exc}）") from exc

    if not grids:
        raise ExcelParseError(f"{name or 'excel'}: 无可解析的可见工作表")

    result = ExcelParseResult(file=name)
    result.warnings.extend(warnings)  # 无缓存公式 warning 已由 reader 层产出

    for grid in grids:
        summary: dict = {
            "sheet": grid.name,
            "regions": 0,
            "rows": 0,
            "blocks": 0,
            "routes": [],
            "hidden_rows": sorted(r + 1 for r in grid.hidden_rows),
            "hidden_cols": sorted(c + 1 for c in grid.hidden_cols),
        }
        regions = split_regions(grid)
        if not regions:
            summary["routes"].append(SheetRoute.EMPTY.value)
            result.warnings.append(f"sheet「{grid.name}」为空")
            result.sheets.append(summary)
            continue

        for region in regions:
            summary["regions"] += 1
            h = detect_header_rows(grid, region)
            route = classify_region(grid, region, h)
            summary["routes"].append(route.value)

            if route is SheetRoute.DOC_SHEET:
                for r in range(region.r1 + h, region.r2 + 1):
                    block = _doc_block_for_row(grid, r, region.c1, region.c2)
                    if block:
                        result.blocks.append(block)
                        summary["blocks"] += 1
            elif route is SheetRoute.FORM_SHEET:
                result.blocks.append(_form_block(grid, region))
                summary["blocks"] += 1
            else:
                keys = flatten_header(grid, region, h)
                marks = mark_total_rows(grid, region, h)
                for i, rec in enumerate(fill_region_values(grid, region, h)):
                    abs_row = region.r1 + h + i
                    data: dict[str, object] = {}
                    for c in sorted(keys):
                        if c in rec:
                            data[keys[c]] = normalize_value(rec[c], grid.formats.get((abs_row, c)))
                    if not data:
                        continue
                    meta: dict[str, object] = {"hidden": abs_row in grid.hidden_rows}
                    if abs_row in marks:
                        meta["from_total"] = True
                        meta["total_evidence"] = marks[abs_row]
                    result.rows.append(
                        RowRecord(
                            sheet=grid.name,
                            row=abs_row + 1,
                            data=data,
                            text=row_text(data),
                            meta=meta,
                        )
                    )
                    summary["rows"] += 1
        result.sheets.append(summary)
    return result
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_parser.py tests/test_excel_models.py tests/test_excel_reader.py tests/test_excel_boundary.py tests/test_excel_header.py tests/test_excel_flatten.py tests/test_excel_values.py tests/test_excel_totals.py -v
```

Expected: 全部 PASS（此前 30 + 本 3）。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/parser.py src/document2chunk/extractors/excel/serializer.py tests/test_excel_parser.py
git commit -m "feat(excel): 解析编排+envelope——毒输入转422、合计打标、文档型块

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: csv 轻量路径 csv_reader.py

**Files:**
- Create: `src/document2chunk/extractors/excel/csv_reader.py`
- Test: `tests/test_excel_csv.py`

**Interfaces:**
- Consumes: `RowRecord/ExcelParseResult`、`row_text`、`clean_key`、`ExcelParseError`
- Produces: `parse_csv_bytes(data: bytes, name: str = "") -> ExcelParseResult`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_csv.py`：

```python
"""csv 轻量路径：编码嗅探 + 分隔符探测 + 单行表头（N4）。"""
from document2chunk.extractors.excel.csv_reader import parse_csv_bytes


def test_red_utf8_csv_basic():
    data = "名称,数量\n甲,1\n乙,2\n".encode("utf-8")
    result = parse_csv_bytes(data, "a.csv")
    assert [r.data for r in result.rows] == [{"名称": "甲", "数量": 1}, {"名称": "乙", "数量": 2}]


def test_red_gbk_and_semicolon():
    data = "名称;数量\n甲;3\n".encode("gbk")
    result = parse_csv_bytes(data, "b.csv")
    assert result.rows[0].data == {"名称": "甲", "数量": 3}


def test_red_duplicate_and_empty_headers():
    data = "名称,,名称\na,1,b\n".encode("utf-8")
    result = parse_csv_bytes(data, "c.csv")
    assert set(result.rows[0].data.keys()) == {"名称", "列2", "名称（2）"}


def test_red_bad_encoding_raises():
    import pytest

    from document2chunk.exceptions import ExcelParseError

    with pytest.raises(ExcelParseError):
        parse_csv_bytes(b"\xff\xfe\x00\x81\x9c", "d.csv")  # 无效 utf-8/gbk 序列
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_csv.py -v
```

Expected: 4 FAIL。

- [ ] **Step 3: 实现 csv_reader.py**

```python
"""csv 轻量路径：编码嗅探（utf-8-sig → gbk）+ 分隔符探测 + 单行表头。无表边界/合并问题（N4）。"""
from __future__ import annotations

import csv
import io

from document2chunk.exceptions import ExcelParseError
from document2chunk.extractors.excel.flatten import clean_key
from document2chunk.extractors.excel.models import ExcelParseResult, RowRecord
from document2chunk.extractors.excel.serializer import row_text

_ENCODINGS = ("utf-8-sig", "gbk")


def _maybe_number(s: str) -> object:
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _decode(data: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ExcelParseError("csv: 无法识别编码（已尝试 utf-8/gbk）")


def parse_csv_bytes(data: bytes, name: str = "") -> ExcelParseResult:
    text = _decode(data)
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delim = max(",;\t", key=sample.count)
    rows = [
        r
        for r in csv.reader(io.StringIO(text), delimiter=delim)
        if any(x.strip() for x in r)
    ]
    if not rows:
        raise ExcelParseError(f"{name or 'csv'}: 空文件")

    header = [clean_key(h) or f"列{i + 1}" for i, h in enumerate(rows[0])]
    seen: dict[str, int] = {}
    for i, k in enumerate(header):
        n = seen.get(k, 0) + 1
        seen[k] = n
        if n > 1:
            header[i] = f"{k}（{n}）"

    result = ExcelParseResult(file=name)
    for line_no, raw in enumerate(rows[1:], start=2):
        rec: dict[str, object] = {}
        for j, key in enumerate(header):
            v = raw[j].strip() if j < len(raw) else ""
            if v:
                rec[key] = _maybe_number(v)
        if rec:
            result.rows.append(RowRecord(sheet="", row=line_no, data=rec, text=row_text(rec)))
    return result
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_csv.py -v
```

Expected: 4 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/extractors/excel/csv_reader.py tests/test_excel_csv.py
git commit -m "feat(excel): csv 轻量路径——编码嗅探+分隔符探测+单行表头

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: serve 接线——指纹路由、soffice 归一化、400 语义更新

**Files:**
- Modify: `src/document2chunk/serve.py`（新增 `parse_excel_to_envelope`；更新 `_xls_err` 文案，serve.py:185-189）
- Modify: `tests/test_serve_legacy.py:100-108`（xlsx/xls 400 断言改为新文案）
- Test: `tests/test_excel_serve.py`

**Interfaces:**
- Consumes: `format_detect.identify/FileKind`、`_legacy_convert`（serve.py:174-177，soffice 间接层，签名以实际为准）
- Produces: `serve.parse_excel_to_envelope(data: bytes, name: str = "") -> dict`——csv 按扩展名走 csv 路径；XLS 指纹（含假 .xlsx）→ soffice 转 xlsx；.et → soffice；其余非 xlsx 内容 → `UnsupportedFormatError`
- 更新后 `_xls_err` 文案：`"检测到表格格式（{kind}）：{name}——请改用 /parse-excel 接口解析表格文件"`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_excel_serve.py`：

```python
"""serve 接线：指纹路由 + soffice 归一化。"""
from types import SimpleNamespace

from tests._excel_fixtures import build_xlsx


def test_red_serve_envelope_for_xlsx():
    from document2chunk import serve

    env = serve.parse_excel_to_envelope(build_xlsx({"s": [["名称", "数量"], ["甲", 1]]}), "a.xlsx")
    assert env["rows"][0]["data"] == {"名称": "甲", "数量": 1}


def test_red_serve_csv_by_extension():
    from document2chunk import serve

    env = serve.parse_excel_to_envelope("名称,数量\n甲,1\n".encode("utf-8"), "a.csv")
    assert env["rows"][0]["data"] == {"名称": "甲", "数量": 1}


def test_red_serve_fake_xlsx_routed_via_legacy_convert(monkeypatch):
    """假 .xlsx（OLE2 指纹）→ soffice 归一化路径（难点19）。soffice 本身由语料冒烟验证。"""
    from document2chunk import serve
    from document2chunk.format_detect import FileKind

    converted = build_xlsx({"s": [["名称", "数量"], ["甲", 1]]})
    calls = {}

    def fake_convert(data, in_ext, target_ext, *, timeout=None, name=None):
        calls["in_ext"] = in_ext
        return converted

    monkeypatch.setattr(serve, "_legacy_convert", fake_convert)
    monkeypatch.setattr(
        serve,
        "identify",
        lambda data, use_magika=True: SimpleNamespace(kind=FileKind.XLS),
    )
    env = serve.parse_excel_to_envelope(b"d0cf11e0-fake-ole2-bytes", "fake.xlsx")
    assert calls["in_ext"] == "xls"
    assert env["rows"][0]["data"] == {"名称": "甲", "数量": 1}


def test_red_serve_unsupported_content_400_type():
    import pytest

    from document2chunk.exceptions import UnsupportedFormatError
    from document2chunk import serve

    with pytest.raises(UnsupportedFormatError):
        serve.parse_excel_to_envelope(b"plain text not excel", "x.xlsx")
```

同时更新 `tests/test_serve_legacy.py:100-108` 两个用例的断言：将既有文案断言（"即将支持"/"导出为 PDF"相关 substring）替换为：

```python
def test_xlsx_suffix_400_with_name():
    """（改造既有用例）chai 管线对表格仍 400，但文案指向 /parse-excel。"""
    ...
    assert "/parse-excel" in str(excinfo.value)
```

（保留用例结构与触发方式不变，只改断言 substring——先读原用例再动手。）

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_excel_serve.py tests/test_serve_legacy.py -v
```

Expected: 4 FAIL（`parse_excel_to_envelope` 不存在）+ legacy 两用例 FAIL（文案未改）。

- [ ] **Step 3: 实现 serve 接线**

`serve.py` 中：

1. `_xls_err`（serve.py:185-189）文案替换：

```python
def _xls_err(name: str, kind: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(
        f"检测到表格格式（{kind}）：{name}——请改用 /parse-excel 接口解析表格文件"
    )
```

2. 新增函数（放在 `_normalize_legacy` 之后）：

```python
def parse_excel_to_envelope(data: bytes, name: str = "") -> dict:
    """Excel/csv 统一入口：指纹路由 + 老格式 soffice 归一化 + 行级 envelope。

    - 扩展名 .csv → csv 轻量路径；
    - XLS 指纹（含 .xls 冒充 .xlsx 的假扩展名）→ soffice 转 xlsx（难点19）；
    - .et 非 xlsx 内容 → soffice 转 xlsx；
    - 其余非 XLSX 内容 → UnsupportedFormatError（chai 层 400）。
    """
    try:
        from document2chunk.extractors.excel.csv_reader import parse_csv_bytes
        from document2chunk.extractors.excel.parser import parse_excel_bytes
        from document2chunk.extractors.excel.serializer import to_envelope
    except ImportError as exc:  # excel extra 未安装
        raise MissingDependencyError(f"excel 解析依赖未安装：pip install '.[excel]'（{exc}）") from exc

    ext = os.path.splitext(name)[1].lower()
    if ext == ".csv":
        return to_envelope(parse_csv_bytes(data, name))

    kind = identify(data).kind
    if ext == ".et" and kind is not FileKind.XLSX:
        data = _legacy_convert(data, "et", "xlsx", name=name)
    elif kind is FileKind.XLS:
        data = _legacy_convert(data, "xls", "xlsx", name=name)
    elif kind is not FileKind.XLSX:
        raise UnsupportedFormatError(
            f"不支持的内容类型 {kind}（ext={ext or '未知'}）：{name or '未命名'}"
        )
    return to_envelope(parse_excel_bytes(data, name))
```

导入核对：`os`、`FileKind`、`identify`、`MissingDependencyError`、`UnsupportedFormatError` 均已在 serve.py 现有 import 中（探索报告确认）；`_legacy_convert(data, in_ext, target_ext, *, name=...)` 的实际签名以 serve.py:174-177 为准，若参数名不同按实际适配（调用处同步改 `fake_convert` 的 monkeypatch 签名）。

- [ ] **Step 4: 运行确认通过**

```bash
uv run -m pytest tests/test_excel_serve.py tests/test_serve_legacy.py tests/test_excel_parser.py -v
```

Expected: 全 PASS（含 legacy 两个改造用例）。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/serve.py tests/test_excel_serve.py tests/test_serve_legacy.py
git commit -m "feat(excel): serve 接线——指纹路由+soffice 归一化，表格 400 文案指向 /parse-excel

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 11: `/parse-excel` 端点 + 真实语料冒烟 + Docker extras

**Files:**
- Modify: `src/document2chunk/api.py`（`create_app()` 内新增端点，与 `/parse-doc` 同区，api.py:553-556 附近）
- Modify: `Dockerfile.d2c`（extras 追加 `excel`）
- Test: `tests/test_parse_excel_api.py`
- Test: `tests/test_excel_corpus_smoke.py`

**Interfaces:**
- Consumes: `serve.parse_excel_to_envelope`（Task 10）
- Produces: `POST /parse-excel`——multipart `file`（或 `file_path`），200 返回行级 envelope JSON；`UnsupportedFormatError`→400、`ExcelParseError`→422（既有 handler 自动映射）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_parse_excel_api.py`（模式对齐 `tests/test_parse_doc_alias.py`）：

```python
"""POST /parse-excel 端点。"""
from fastapi.testclient import TestClient

from document2chunk import api as api_mod

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _client() -> TestClient:
    return TestClient(api_mod.create_app())


def test_red_openapi_has_parse_excel():
    client = _client()
    assert "/parse-excel" in client.get("/openapi.json").json()["paths"]


def test_red_parse_excel_ok():
    from tests._excel_fixtures import build_xlsx

    client = _client()
    r = client.post(
        "/parse-excel",
        files={"file": ("a.xlsx", build_xlsx({"s": [["名称", "数量"], ["甲", 1]]}), _XLSX_MIME)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["file"] == "a.xlsx"
    assert body["rows"][0]["data"] == {"名称": "甲", "数量": 1}
    assert "warnings" in body and "blocks" in body and "sheets" in body


def test_red_parse_excel_garbage_content_400():
    client = _client()
    r = client.post("/parse-excel", files={"file": ("x.xlsx", b"plain text not excel", "application/octet-stream")})
    assert r.status_code == 400        # UnsupportedFormatError 既有映射
    assert "detail" in r.json()


def test_red_parse_excel_broken_zip_422():
    client = _client()
    r = client.post("/parse-excel", files={"file": ("x.xlsx", b"PK\x03\x04broken", "application/octet-stream")})
    assert r.status_code == 422


def test_red_parse_excel_requires_file_or_path():
    client = _client()
    r = client.post("/parse-excel", data={})
    assert r.status_code == 400
```

创建 `tests/test_excel_corpus_smoke.py`（真实语料守卫，绝对路径+skip 模式对齐 `tests/test_table_geo_ocr.py:22-23`）：

```python
"""P0 语料冒烟（Excel语料盘点-draft.md §2 P0 5 件）。本机无语料时整文件 skip。"""
import shutil
from pathlib import Path

import pytest

from document2chunk import serve

_ROOT = Path("D:/project/kxx-docs")

_P0_XLSX = [
    "2.1-2022年下半年集团审批类项目初步评审_202208251638（调序后）.xlsx",
    "项目自定义导出数据 (3) -项目清单2026.3.3.xlsx",
    "项目自定义导出数据 (基础)-最新.xlsx",
    "7-广东省高速公路改扩建关键技术研究-R&D经费决算表及支出说明（必选）.xlsx",
]
_P0_FAKE_XLSX = "20- 软弱地层挤扩锚固关键技术研究-其他.xlsx"


@pytest.mark.parametrize("fname", _P0_XLSX)
def test_red_p0_smoke(fname: str):
    p = _ROOT / fname
    if not p.is_file():
        pytest.skip(f"本机无语料: {fname}")
    env = serve.parse_excel_to_envelope(p.read_bytes(), fname)
    assert env["rows"], f"{fname} 应产出数据行"
    for row in env["rows"]:
        assert row["data"], "每行 data 非空"
        assert row["text"], "每行 text 非空"


def test_red_p0_fake_xlsx_needs_soffice():
    if shutil.which("soffice") is None:
        pytest.skip("本机无 soffice，无法验证假 .xlsx 归一化")
    p = _ROOT / _P0_FAKE_XLSX
    if not p.is_file():
        pytest.skip("本机无语料")
    env = serve.parse_excel_to_envelope(p.read_bytes(), _P0_FAKE_XLSX)
    assert env["rows"], "假 .xlsx 经 soffice 归一化后应可解析"
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run -m pytest tests/test_parse_excel_api.py tests/test_excel_corpus_smoke.py -v
```

Expected: api 5 FAIL（端点不存在）；语料冒烟按本机情况 PASS（语料在 D 盘且代码未变）或 skip——冒烟测试此处先确立基线，端点实现后必须仍 PASS。

- [ ] **Step 3: 实现端点与 Dockerfile**

`api.py`：在 `/parse-doc` 端点（api.py:553-556）之后新增（`serve` 的导入方式与 `_chai_parse` 内一致；`JSONResponse` 若未导入则从 `fastapi.responses` 补充）：

```python
    @app.post("/parse-excel")
    async def _parse_excel(request: Request) -> Response:
        """Excel/csv → 行级 JSON envelope（轨 A: chunk2embedding）。"""
        form = await request.form()
        upload = form.get("file")
        if upload is not None:
            data = await upload.read()
            name = getattr(upload, "filename", None) or "upload.xlsx"
        else:
            file_path = form.get("file_path")
            if not file_path:
                raise HTTPException(status_code=400, detail="需要 multipart 字段 file 或 file_path")
            p = Path(str(file_path))
            if not p.is_file():
                raise HTTPException(status_code=400, detail=f"文件不存在: {file_path}")
            data = p.read_bytes()
            name = p.name
        with _PARSE_CONCURRENCY:
            envelope = await run_in_threadpool(serve_mod.parse_excel_to_envelope, data, name)
        return JSONResponse(envelope)
```

（`Path` 需 `from pathlib import Path`；并发信号量 `_PARSE_CONCURRENCY`、`run_in_threadpool` 沿用 api.py:36-39/69-86 既有实现；观测三件套 `log_arrive/StageTimer/finish` 按 `_chai_parse`（api.py:442-519）的既有调用形补齐，签名以 timing.py 实际为准。）

`Dockerfile.d2c`：`pip install ".[pdf,ocr,docx,api,viz,legacy]"` → `pip install ".[pdf,ocr,docx,api,viz,legacy,excel]"`。

- [ ] **Step 4: 全量回归**

```bash
uv run -m pytest -q
```

Expected: 全部 PASS（既有测试无回归；`test_serve_legacy` 的两处改造已随 Task 10 完成）。

- [ ] **Step 5: Commit**

```bash
git add src/document2chunk/api.py Dockerfile.d2c tests/test_parse_excel_api.py tests/test_excel_corpus_smoke.py
git commit -m "feat(api): 新增 /parse-excel 接口——行级 JSON envelope+P0 语料冒烟

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 验收对照（定档表 P0 → 任务映射）

| P0 难点 | 落在任务 |
|---|---|
| #1 合并单元格 / #3 多行表头 | Task 4/5（检测+扁平化+下放） |
| #2 表边界 | Task 3 |
| #5 dimension 反向 | Task 2（calamine 实际值域，不信声明） |
| #7 公式缓存缺失 | Task 2（探测）+ Task 8（warning） |
| #15 重复/空列名 | Task 5（`（2）`/`列N`）+ Task 9（csv 同规则） |
| #19 假扩展名 | Task 10（指纹→soffice）+ Task 11 冒烟 |
| N1 空 sheet / N2 `~$` 锁文件 | Task 8（空表 warning）；`~$` 拒收由既有 `_needs_legacy` 后缀路径覆盖（若有独立 `~$` 拦截需求，随验收补一行守卫测试） |
| N5 文档型 / N6 表单式 | Task 4（分流）+ Task 8（blocks 产出） |
| 轨 A 其余 P1（隐藏打标/合计打标/透视告警/外链告警/csv 编码） | Task 2/7/8/9 |

P1 剩余项（单元格换行值处理、第三方生成器兼容、50 万行性能基准、合成用例回归库）与全部 P2 项见 `Document2Chunk-Excel难点定档表.md` §3/§4，不在本计划范围。

## 明确不做（YAGNI）

- 不接入 Document IR / postprocess pipeline（契约不同，见 Architecture）
- 不做 chunk 切分与 embedding（下游职责）
- 不做轨 B 任何增量（类型推断/DDL/合计剔除——二期）
- 不做 Excel → HTML/Markdown 整表输出
- 不解密加密文件（政策：拒收）




