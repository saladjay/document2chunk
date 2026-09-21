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
from document2chunk.extractors.excel.totals import classify_total_rows, collect_total_signals
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
                definite, candidate = classify_total_rows(
                    collect_total_signals(grid, region, h)
                )
                for i, rec in enumerate(fill_region_values(grid, region, h)):
                    abs_row = region.r1 + h + i
                    data: dict[str, object] = {}
                    for c in sorted(keys):
                        if c in rec:
                            data[keys[c]] = normalize_value(rec[c], grid.formats.get((abs_row, c)))
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
        result.sheets.append(summary)
    return result
