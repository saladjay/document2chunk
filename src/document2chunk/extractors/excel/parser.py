"""解析编排：xlsx bytes → ExcelParseResult（轨 A: chunk2embedding）。"""
from __future__ import annotations

from document2chunk.exceptions import ExcelParseError
from document2chunk.extractors.excel.boundary import split_regions
from document2chunk.extractors.excel.flatten import (
    build_merge_origin,
    cell_value,
    fill_region_values,
    flatten_header,
)
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
from document2chunk.extractors.excel.totals import classify_total_rows, collect_total_signals
from document2chunk.extractors.excel.values import normalize_value


def _is_blank(v: object) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _row_str_vals(grid: SheetGrid, r: int, c1: int, c2: int) -> list[str]:
    row = grid.values[r] if r < grid.n_rows else []
    return [str(v).strip() for v in row[c1 : c2 + 1] if not _is_blank(v)]


def _doc_block_for_row(
    grid: SheetGrid, r: int, c1: int, c2: int, **extra_meta: object
) -> SheetBlock | None:
    vals = _row_str_vals(grid, r, c1, c2)
    if not vals:
        return None
    text = f"{vals[0]}: {vals[1]}" if len(vals) >= 2 else vals[0]
    meta: dict[str, object] = {"row": r + 1}
    meta.update(extra_meta)
    return SheetBlock(sheet=grid.name, kind="doc", text=text, meta=meta)


def _fallback_doc_blocks(grid: SheetGrid, region: Region) -> list[SheetBlock]:
    """零产出区域兜底（Q3 铁律）：合并值下放后逐行成 doc 块。"""
    origin = build_merge_origin(grid)
    blocks: list[SheetBlock] = []
    for r in range(region.r1, region.r2 + 1):
        vals = [
            str(cell_value(grid, origin, r, c)).strip()
            for c in range(region.c1, region.c2 + 1)
            if not _is_blank(cell_value(grid, origin, r, c))
        ]
        if vals:
            text = f"{vals[0]}: {vals[1]}" if len(vals) >= 2 else vals[0]
            blocks.append(
                SheetBlock(
                    sheet=grid.name,
                    kind="doc",
                    text=text,
                    meta={"row": r + 1, "role": "orphan_region"},
                )
            )
    return blocks


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

        dt_regions: list[tuple[Region, dict[int, str]]] = []
        for region in regions:
            summary["regions"] += 1
            rows_before = len(result.rows)
            blocks_before = len(result.blocks)
            h = detect_header_rows(grid, region)
            route = classify_region(grid, region, h)
            summary["routes"].append(route.value)

            if route is SheetRoute.DOC_SHEET:
                # 表头行救援（18 号教训）：真实内容被当标题行丢弃不可无声；
                # 短列标签（如 问题/答复，值均 ≤4 字）按设计折叠不救援（11 号定稿）
                for hr in range(region.r1, region.r1 + h):
                    vals = _row_str_vals(grid, hr, region.c1, region.c2)
                    if vals and max(len(v) for v in vals) > 4:
                        result.blocks.append(
                            _doc_block_for_row(
                                grid, hr, region.c1, region.c2, role="sheet_header"
                            )
                        )
                        summary["blocks"] += 1
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
                dt_regions.append((region, keys))
                definite, candidate = classify_total_rows(
                    collect_total_signals(grid, region, h)
                )
                for i, rec in enumerate(fill_region_values(grid, region, h)):
                    abs_row = region.r1 + h + i
                    data: dict[str, object] = {}
                    for c in sorted(keys):
                        if c in rec:
                            data[keys[c]] = normalize_value(
                                rec[c], grid.formats.get((abs_row, c))
                            )
                    if not data:
                        continue
                    meta: dict[str, object] = {"hidden": abs_row in grid.hidden_rows}
                    if abs_row in definite:
                        meta["from_total"] = True
                        meta["total_evidence"] = definite[abs_row]
                    elif abs_row in candidate:
                        meta["from_total_candidate"] = True
                        meta["total_evidence"] = candidate[abs_row]
                        result.warnings.append(
                            f"sheet「{grid.name}」第 {abs_row + 1} 行疑似合计行"
                            f"（证据：{'/'.join(candidate[abs_row])}），未打 from_total"
                        )
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

            # Q3 铁律：区域 0 行 0 块 → 内容必有去处（兜底成块 + 留痕）
            if len(result.rows) == rows_before and len(result.blocks) == blocks_before:
                fallback = _fallback_doc_blocks(grid, region)
                if fallback:
                    result.blocks.extend(fallback)
                    summary["blocks"] += len(fallback)
                    result.warnings.append(
                        f"sheet「{grid.name}」第 {region.r1 + 1}-{region.r2 + 1} 行"
                        f"区域未按表格产出，已兜底为 {len(fallback)} 个文本块"
                    )

        # 同 sheet 等宽区域键结构不一致告警（04 号「次表继承首表错误路径」的探测器）
        for (ra, ka), (rb, kb) in zip(dt_regions, dt_regions[1:]):
            if (ra.c2 - ra.c1) == (rb.c2 - rb.c1) and sorted(ka.values()) != sorted(kb.values()):
                result.warnings.append(
                    f"sheet「{grid.name}」第 {rb.r1 + 1} 行起的区域与"
                    f"第 {ra.r1 + 1} 行起的区域表头结构不一致"
                )
        result.sheets.append(summary)
    return result
