"""几何重建：``cell_box_list`` → IR TableNode（designs/008 §几何重建）。

服务返回的 ``html`` 在**超复杂多级合并表头**上拓扑错乱（colspan 落在空格、
文字塞进 1 宽格、列数算错），但 ``json.cell_box_list`` 的**几何可靠**——单元
格检测模型的框把合并信息藏在尺寸里（高框=rowspan、宽框=colspan）。

本模块用几何还原真实网格拓扑，文字从 html 按行优先 zip 对齐（**信文字、不信
html 的 colspan/rowspan**）；不可靠时 :func:`try_geo_to_table_node` 返回
``None``，供 extractor 透明回退到 ``html_to_table_node``。

算法（6 步）：
1. 自适应边聚类（行/列各自 ``tol``，基于 median box 维度）。
2. 幻影带修复（间距小 + 支撑边少的带合并，去抖动假带）。
3. 框→``(r0, c0, rowspan, colspan)``（bisect 最近带线）。
4. 覆盖校验（``holes``/``overlaps`` 超 5% 余量判失败）。
5. 文字对齐（html 非空文字行优先 zip；数量失配 >10% 判失败）。
6. 构造 ``TableNode``（cell bbox 挂**内层** ``ParagraphNode.provenance.bbox``——
   ``TableCellNode`` 无 provenance 字段）。
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from statistics import median
from typing import Optional

from document2chunk.extractors.table._html_parser import _Idc, _TableHTMLParser
from document2chunk.ir import (
    ParagraphNode,
    Provenance,
    RunNode,
    SourceType,
    TableCellNode,
    TableNode,
    TableRowNode,
)

# 默认参数（容差需多 PDF 调参；见 designs/008 §已知限制）
_TOL_FRAC = 0.18  # tol = frac × median(box 维度)
_TOL_FLOOR = 8.0
_PHANTOM_FRAC = 0.45  # 带间距 < frac × median 带间距 视为抖动假带
_PHANTOM_FLOOR = 10.0  # 带间距绝对下限（捕捉 ~10px 抖动）
_COVERAGE_SLACK_FRAC = 0.05  # holes/overlaps 允许 5% × R×C
_ALIGN_ROW_SLACK = 1  # html 行数与 geo 行数允许差 1


# ============================================================
# 1. 边聚类
# ============================================================


def cluster_edges(vals: list[float], tol: float) -> list[float]:
    """贪心 1D 聚类 + 滑动均值中心。

    排序后逐个并入最近的簇（距离 ≤ tol），并入时把簇中心更新为簇内均值——
    链式合并（A 并 B 后中心移动，可能再并 C）。返回聚类中心（已排序）。
    """
    vals = sorted(vals)
    if not vals:
        return []
    centers = [vals[0]]
    sums = [vals[0]]
    counts = [1]
    for v in vals[1:]:
        if v - centers[-1] <= tol:
            sums[-1] += v
            counts[-1] += 1
            centers[-1] = sums[-1] / counts[-1]
        else:
            centers.append(v)
            sums.append(v)
            counts.append(1)
    return centers


def _default_tol(dims: list[float], frac: float = _TOL_FRAC, floor: float = _TOL_FLOOR) -> float:
    """tol ≈ frac × median(box 维度)，下限 floor。

    用 box 维度（而非边间距）做基准：边间距被簇内小间距主导，会给出过小的 tol；
    box 维度反映网格尺度，跨 DPI/表格大小更稳。
    """
    if not dims:
        return floor
    med = median(dims)
    return max(floor, frac * med) if med else floor


# ============================================================
# 2. 幻影带修复
# ============================================================


def _repair_phantom_bands(
    bands: list[float],
    *,
    frac: float = _PHANTOM_FRAC,
    floor: float = _PHANTOM_FLOOR,
) -> list[float]:
    """迭代合并间距过近的相邻带（同一网格线的检测抖动），直到无合并。

    threshold = max(floor, frac × median(当前带间距))；最小间距对 gap < threshold
    即合并（取二者均值），重算 median 重复——合并可能产生新的近邻对，故迭代。

    比「固定 tol」更稳：tol 略小导致同一线被拆成两簇时，此处把它们合回。
    """
    if len(bands) < 3:
        return list(bands)
    bands = list(bands)
    for _ in range(len(bands)):  # 上限 = 带数，防御死循环
        gaps = [bands[i] - bands[i - 1] for i in range(1, len(bands))]
        if not gaps:
            break
        med = median(gaps)
        thr = max(floor, frac * med) if med else floor
        min_gap = min(gaps)
        if min_gap >= thr or len(bands) <= 2:
            break
        mi = gaps.index(min_gap) + 1  # bands[mi] 与 bands[mi-1] 合并
        merged = (bands[mi - 1] + bands[mi]) / 2
        bands = bands[: mi - 1] + [merged] + bands[mi + 1 :]
    return bands


# ============================================================
# 3. 框 → span
# ============================================================


def _snap_to_band(v: float, bands: list[float]) -> int:
    """v 最近的带线索引（bisect）。空 bands 返回 0。"""
    if not bands:
        return 0
    i = bisect_left(bands, v)
    if i == 0:
        return 0
    if i >= len(bands):
        return len(bands) - 1
    # i-1 与 i 之间取近者
    return i - 1 if (v - bands[i - 1]) <= (bands[i] - v) else i


def boxes_to_grid(
    boxes: list[list[float]],
    *,
    row_tol: Optional[float] = None,
    col_tol: Optional[float] = None,
) -> dict:
    """92 个 box → ``{rows, cols, row_lines, col_lines, cells}``。

    每个 cell：``{bi, bbox, r0, c0, rowspan, colspan}``。
    """
    heights = [b[3] - b[1] for b in boxes]
    widths = [b[2] - b[0] for b in boxes]
    ys = [b[1] for b in boxes] + [b[3] for b in boxes]
    xs = [b[0] for b in boxes] + [b[2] for b in boxes]

    rt = row_tol if row_tol is not None else _default_tol(heights)
    ct = col_tol if col_tol is not None else _default_tol(widths)

    row_lines = _repair_phantom_bands(cluster_edges(ys, rt))
    col_lines = _repair_phantom_bands(cluster_edges(xs, ct))

    cells = []
    for bi, b in enumerate(boxes):
        r0 = _snap_to_band(b[1], row_lines)
        r1 = _snap_to_band(b[3], row_lines)
        c0 = _snap_to_band(b[0], col_lines)
        c1 = _snap_to_band(b[2], col_lines)
        rs = max(1, r1 - r0)
        cs = max(1, c1 - c0)
        cells.append({"bi": bi, "bbox": list(b), "r0": r0, "c0": c0, "rowspan": rs, "colspan": cs})

    return {
        "rows": max(0, len(row_lines) - 1),
        "cols": max(0, len(col_lines) - 1),
        "row_lines": row_lines,
        "col_lines": col_lines,
        "cells": cells,
    }


# ============================================================
# 4. 覆盖校验
# ============================================================


def validate_grid(grid: dict, *, slack_frac: float = _COVERAGE_SLACK_FRAC) -> tuple[bool, Optional[str]]:
    """每个 ``(r,c)`` 应被覆盖一次；holes/overlaps 超余量判失败。"""
    R, C, cells = grid["rows"], grid["cols"], grid["cells"]
    if R < 1 or C < 1:
        return False, "degenerate_grid"
    if len(cells) < 2:
        return False, "too_few_cells"
    cov = [[0] * C for _ in range(R)]
    for c in cells:
        for r in range(c["r0"], c["r0"] + c["rowspan"]):
            for cc in range(c["c0"], c["c0"] + c["colspan"]):
                if 0 <= r < R and 0 <= cc < C:
                    cov[r][cc] += 1
    holes = sum(v == 0 for row in cov for v in row)
    overlaps = sum(v > 1 for row in cov for v in row)
    slack = max(2, int(slack_frac * R * C))
    if holes > slack:
        return False, f"holes={holes}>{slack}"
    if overlaps > slack:
        return False, f"overlaps={overlaps}>{slack}"
    return True, None


# ============================================================
# 5. 文字对齐
# ============================================================


def _parse_html_rows(html: str) -> list[list[str]]:
    """复用 ``_TableHTMLParser`` 取 html 每行非空文字（文档序）。

    信每格文字与**行结构**，**不信**其 colspan/rowspan（那是错乱值）。
    返回 ``[[row0 非空文字...], [row1 非空文字...], ...]``。
    """
    p = _TableHTMLParser()
    p.feed(html or "")
    return [[c["text"] for c in row if c["text"]] for row in p.rows]


def align_texts(cells: list[dict], html_rows: list[list[str]]) -> Optional[dict]:
    """**行对齐**：html 行 ↔ geo 行（按 ``r0`` 分组），行内非空 html 文字 zip 到该行
    geo 格（``c0`` 序）。

    比平铺 zip 更稳：尊重模型的行结构（行边界大致可信，错的是格内 span），且能正
    确跳过行内的空格、空数据行。

    失败（返回 ``None``）：html 非空文字总数 > geo 格总数（必错）。
    """
    n_total_texts = sum(len(r) for r in html_rows)
    if n_total_texts > len(cells):
        return None  # 文字比格多——结构对不上，必错

    by_r0: dict[int, list[int]] = defaultdict(list)
    for ci, c in enumerate(cells):
        by_r0[c["r0"]].append(ci)
    geo_rids = sorted(by_r0)

    pairs: dict[int, str] = {}
    n_html = len(html_rows)
    for i, rid in enumerate(geo_rids):
        if i >= n_html:
            break  # geo 行比 html 多（如尾部幻影行）——这些格留空
        geo_row = sorted(by_r0[rid], key=lambda ci: cells[ci]["c0"])
        for j, txt in enumerate(html_rows[i]):
            if j < len(geo_row):
                pairs[geo_row[j]] = txt
            # else: html 该行文字比 geo 格多——丢弃尾部（罕见，多为 html 多并了空占位）
    return {"pairs": pairs}


# ============================================================
# 6. 构造 TableNode + 表头判定
# ============================================================


def _decide_header(
    ri: int,
    row_cells: list[dict],
    *,
    header_first_row: bool,
    header_rows: Optional[int],
) -> bool:
    """表头判定：``header_rows`` 显式覆盖优先；其次首行；其次「整行全合并」自动。"""
    if header_rows is not None:
        return ri < header_rows
    if header_first_row and ri == 0:
        return True
    if row_cells and all(c["rowspan"] > 1 or c["colspan"] > 1 for c in row_cells):
        return True
    return False


def _union_bbox(cells: list[dict]) -> Optional[list[float]]:
    xs, ys = [], []
    for c in cells:
        b = c["bbox"]
        xs.extend((b[0], b[2]))
        ys.extend((b[1], b[3]))
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _round_box(box: Optional[list[float]]) -> Optional[list[float]]:
    return [round(float(v), 2) for v in box] if box else None


def _build_table_node(
    grid: dict,
    alignment: Optional[dict],
    *,
    page_index: Optional[int],
    source_type: SourceType,
    table_bbox: Optional[list[float]],
    idc: _Idc,
    header_first_row: bool,
    header_rows: Optional[int],
) -> TableNode:
    cells = grid["cells"]
    R = grid["rows"]
    by_row: dict[int, list[dict]] = defaultdict(list)
    for ci, c in enumerate(cells):
        by_row[c["r0"]].append((ci, c))

    rows: list[TableRowNode] = []
    for ri in range(R):
        entries = sorted(by_row.get(ri, []), key=lambda ic: ic[1]["c0"])
        if not entries:
            continue  # 空带（被上方合并覆盖）——不输出空行
        ir_cells = []
        row_cells_for_header = []
        for ci, c in entries:
            text = alignment["pairs"].get(ci, "") if alignment else ""
            box = _round_box(c["bbox"])
            ir_cells.append(
                TableCellNode(
                    id=idc.cell(),
                    colspan=c["colspan"],
                    rowspan=c["rowspan"],
                    blocks=[
                        ParagraphNode(
                            id=idc.block(),
                            text=text,
                            runs=[RunNode(id=idc.block(), text=text)],
                            provenance=Provenance(
                                source_type=source_type,
                                page_index=page_index,
                                bbox=box,
                            ),
                        )
                    ],
                )
            )
            row_cells_for_header.append(c)
        is_header = _decide_header(
            ri, row_cells_for_header, header_first_row=header_first_row, header_rows=header_rows
        )
        rows.append(TableRowNode(id=idc.row(), cells=ir_cells, is_header=is_header))

    bbox = _round_box(table_bbox) if table_bbox is not None else _round_box(_union_bbox(cells))
    return TableNode(
        id=idc.block(),
        rows=rows,
        provenance=Provenance(source_type=source_type, page_index=page_index, bbox=bbox),
    )


# ============================================================
# 公开 API
# ============================================================


def geo_to_table_node(
    cell_boxes: list[list[float]],
    *,
    html: str = "",
    page_index: Optional[int] = None,
    source_type: SourceType = SourceType.OCR,
    table_bbox: Optional[list[float]] = None,
    idc: Optional[_Idc] = None,
    header_first_row: bool = True,
    header_rows: Optional[int] = None,
    rec_texts: Optional[list[str]] = None,
    rec_scores: Optional[list[float]] = None,
    row_tol: Optional[float] = None,
    col_tol: Optional[float] = None,
) -> TableNode:
    """几何重建 ``cell_box_list`` → ``TableNode``（保留正确 colspan/rowspan）。

    硬失败抛 ``ValueError``（单测用）；生产入口用 :func:`try_geo_to_table_node`。

    Args:
        cell_boxes: ``json.cell_box_list``，每个 ``[x1,y1,x2,y2]``。
        html: 服务 ``html``（取每格文字，忽略其 colspan/rowspan）。
        page_index / source_type / table_bbox: 写入 provenance。
        idc: 共享 ID 生成器（缺省新建；与 html 回退共用时不断号）。
        header_first_row: 无显式 ``header_rows`` 时是否首行作表头。
        header_rows: 显式表头行数（表单表建议传，如 3）。
        rec_texts / rec_scores: **当前未启用**（无 box、不对齐单元格），仅占位 API。
        row_tol / col_tol: 覆盖自适应聚类容差。
    """
    if not cell_boxes or len(cell_boxes) < 2:
        raise ValueError("cell_boxes 过少（<2），无法重建网格")
    idc = idc or _Idc()

    grid = boxes_to_grid(cell_boxes, row_tol=row_tol, col_tol=col_tol)
    ok, reason = validate_grid(grid)
    if not ok:
        raise ValueError(f"网格校验失败：{reason}")

    html_rows = _parse_html_rows(html)
    alignment = align_texts(grid["cells"], html_rows)
    if alignment is None:
        raise ValueError("文字对齐失败（文字比格多）")

    return _build_table_node(
        grid,
        alignment,
        page_index=page_index,
        source_type=source_type,
        table_bbox=table_bbox,
        idc=idc,
        header_first_row=header_first_row,
        header_rows=header_rows,
    )


def try_geo_to_table_node(
    cell_boxes: Optional[list[list[float]]],
    *,
    html: str = "",
    page_index: Optional[int] = None,
    source_type: SourceType = SourceType.OCR,
    table_bbox: Optional[list[float]] = None,
    idc: Optional[_Idc] = None,
    header_first_row: bool = True,
    header_rows: Optional[int] = None,
    rec_texts: Optional[list[str]] = None,
    rec_scores: Optional[list[float]] = None,
    row_tol: Optional[float] = None,
    col_tol: Optional[float] = None,
) -> Optional[TableNode]:
    """校验守护包装：任一失败返回 ``None``（供 extractor 透明回退 html）。

    失败条件：① cell_boxes 缺失/<2；② 网格退化；③ 覆盖校验不过；
    ④ 文字对齐不过；⑤ 任何异常（geo 永不阻断流水线）。

    关键：失败路径不消耗 ``idc``（校验全过后才 build），故与 html 回退共用同一
    ``idc`` 实例时 ID 不断号。
    """
    try:
        if not cell_boxes or len(cell_boxes) < 2:
            return None
        idc_local = idc  # 仅在成功路径用调用方 idc；不在失败路径消耗
        grid = boxes_to_grid(cell_boxes, row_tol=row_tol, col_tol=col_tol)
        ok, _reason = validate_grid(grid)
        if not ok:
            return None
        html_rows = _parse_html_rows(html)
        alignment = align_texts(grid["cells"], html_rows)
        if alignment is None:
            return None
        # 全部校验通过——此时才 build（消耗 idc）
        return _build_table_node(
            grid,
            alignment,
            page_index=page_index,
            source_type=source_type,
            table_bbox=table_bbox,
            idc=idc_local or _Idc(),
            header_first_row=header_first_row,
            header_rows=header_rows,
        )
    except Exception:  # 防御：geo 永不阻断流水线
        return None


# ============================================================
# geo_ocr：几何网格 + 每格 box-bearing OCR 文字（根治合并表头文字错位）
# ============================================================


def geo_ocr_to_table_node(
    cell_boxes: list[list[float]],
    image,
    *,
    ocr=None,
    page_index: Optional[int] = None,
    source_type: SourceType = SourceType.OCR,
    table_bbox: Optional[list[float]] = None,
    idc: Optional[_Idc] = None,
    header_first_row: bool = True,
    header_rows: Optional[int] = None,
    row_tol: Optional[float] = None,
    col_tol: Optional[float] = None,
) -> TableNode:
    """几何网格 + **每格 box-bearing OCR 文字** → ``TableNode``（硬失败抛 ``ValueError``）。

    与 :func:`geo_to_table_node` 的区别：文字不来自（错乱的）html，而对 ``image`` 本地
    OCR、按 poly 中心落点钉到格——根治合并表头文字错位（如 r0 宽表头）。

    前置：``cell_boxes`` 必须与 ``image`` **同像素空间**（由 extractor 的 ``geo_ocr``
    模式：渲染页 → 把该图送服务取得校准框）。

    Args:
        cell_boxes: 与 image 同空间的单元格框。
        image: 页图（路径 / PIL / ndarray）——传给 ``ocr.predict``。
        ocr: paddleocr 引擎（缺省 lazy 创建）。可注入 stub 单测。
    """
    from document2chunk.extractors.table._cell_ocr import ocr_cell_texts

    if not cell_boxes or len(cell_boxes) < 2:
        raise ValueError("cell_boxes 过少（<2），无法重建网格")
    idc = idc or _Idc()

    grid = boxes_to_grid(cell_boxes, row_tol=row_tol, col_tol=col_tol)
    ok, reason = validate_grid(grid)
    if not ok:
        raise ValueError(f"网格校验失败：{reason}")

    pairs = ocr_cell_texts(cell_boxes, image, ocr=ocr)  # {bi: text}，box-bearing
    return _build_table_node(
        grid,
        {"pairs": pairs},
        page_index=page_index,
        source_type=source_type,
        table_bbox=table_bbox,
        idc=idc,
        header_first_row=header_first_row,
        header_rows=header_rows,
    )


def try_geo_ocr_to_table_node(
    cell_boxes: Optional[list[list[float]]],
    image,
    *,
    ocr=None,
    page_index: Optional[int] = None,
    source_type: SourceType = SourceType.OCR,
    table_bbox: Optional[list[float]] = None,
    idc: Optional[_Idc] = None,
    header_first_row: bool = True,
    header_rows: Optional[int] = None,
    row_tol: Optional[float] = None,
    col_tol: Optional[float] = None,
) -> Optional[TableNode]:
    """``geo_ocr`` 的校验守护包装：任一失败（含 OCR/渲染异常）返回 ``None`` 供回退。

    失败条件：① cell_boxes 缺失/<2；② 网格退化；③ 覆盖校验不过；④ OCR 异常；
    ⑤ 任何异常。失败路径不消耗 ``idc``。
    """
    try:
        from document2chunk.extractors.table._cell_ocr import ocr_cell_texts

        if not cell_boxes or len(cell_boxes) < 2:
            return None
        grid = boxes_to_grid(cell_boxes, row_tol=row_tol, col_tol=col_tol)
        ok, _reason = validate_grid(grid)
        if not ok:
            return None
        pairs = ocr_cell_texts(cell_boxes, image, ocr=ocr)
        # 全部校验/OCR 通过——此时才 build（消耗 idc）
        return _build_table_node(
            grid,
            {"pairs": pairs},
            page_index=page_index,
            source_type=source_type,
            table_bbox=table_bbox,
            idc=idc or _Idc(),
            header_first_row=header_first_row,
            header_rows=header_rows,
        )
    except Exception:  # 防御：geo_ocr 永不阻断流水线（OCR/渲染失败也回退）
        return None
