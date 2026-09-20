# -*- coding: utf-8 -*-
"""Excel 语料结构特征扫描（只读，绝不修改源文件）。

用法:
  uv run --with openpyxl python scripts/_corpus_scan_excel.py <corpus_dir> [-o out.json]

对每个 Excel 类文件提取结构特征，输出 JSON + 人读摘要。
.et / 老 .xls openpyxl 读不了 -> 仅计数并标记 NEED_SOFFICE_CONVERT。
"""
from __future__ import annotations

import argparse
import csv as _csv
import json
import os
import sys
import zipfile
from pathlib import Path

OPENPYXL_EXTS = {".xlsx", ".xlsm"}
NEED_CONVERT_EXTS = {".xls", ".et"}  # openpyxl 不支持，需 soffice 转换后复扫
CSV_EXTS = {".csv"}
LOCK_PREFIX = "~$"

TOTAL_ROW_WORDS = ("合计", "总计", "小计", "汇总", "total")


def sniff_encoding(raw: bytes) -> str:
    """粗编码嗅探：BOM -> utf-8 尝试 -> gbk 尝试 -> latin-1 兜底。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        raw.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        pass
    return "latin-1"


def sniff_delimiter(sample: str) -> str:
    try:
        dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        # 中文语料里逗号频次常被正文干扰，按频次兜底
        counts = {d: sample.count(d) for d in ",;\t|"}
        return max(counts, key=counts.get) if any(counts.values()) else ","


def zip_features(path: Path) -> dict:
    """zip 包级别的特征（不依赖 openpyxl）。"""
    feats = {
        "has_external_links": False,
        "external_link_count": 0,
        "has_pivot_tables": False,
        "pivot_table_count": 0,
        "has_chartsheet": False,
        "has_vba": False,
        "zip_parts": [],
    }
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            feats["zip_parts"] = names[:400]
            ext = [n for n in names if n.startswith("xl/externalLinks/")]
            feats["external_link_count"] = len({n for n in ext if n.endswith(".xml")})
            feats["has_external_links"] = feats["external_link_count"] > 0
            pv = [n for n in names if n.startswith("xl/pivotTables/")]
            feats["pivot_table_count"] = len({n for n in pv if n.endswith(".xml")})
            feats["has_pivot_tables"] = feats["pivot_table_count"] > 0
            feats["has_chartsheet"] = any(n.startswith("xl/chartsheets/") for n in names)
            feats["has_vba"] = any(n.endswith("vbaProject.bin") for n in names)
    except zipfile.BadZipFile:
        feats["error"] = "BadZipFile: 不是有效的 OOXML zip（可能是假 xlsx / 老 xls 改名）"
    except Exception as e:  # noqa: BLE001
        feats["error"] = f"zip 读取失败: {type(e).__name__}: {e}"
    return feats


def col_letter_to_idx(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - 64)
    return n


def parse_dim(dim: str):
    """'A1:D50' -> (1,1,50,4) 行列数；解析失败返回 None。"""
    if not dim or ":" not in dim:
        return None
    try:
        a, b = dim.split(":")
        ra = int("".join(c for c in a if c.isdigit()))
        rb = int("".join(c for c in b if c.isdigit()))
        la = "".join(c for c in a if c.isalpha()).upper()
        lb = "".join(c for c in b if c.isalpha()).upper()
        return (rb - ra + 1, col_letter_to_idx(lb) - col_letter_to_idx(la) + 1)
    except Exception:
        return None


def sheet_features(ws) -> dict:
    """单 sheet 特征。普通（非 read_only）模式。"""
    f = {
        "name": ws.title,
        "state": ws.sheet_state,
        "declared_dim": None,
        "declared_rows": None,
        "declared_cols": None,
        "actual_rows": 0,
        "actual_cols": 0,
        "ghost_used_range": False,
        "ghost_ratio": None,
        "merged_count": 0,
        "merged_in_header": 0,
        "header_rows": 0,
        "header_multi_row_suspect": False,
        "formula_cells": 0,
        "hidden_rows": 0,
        "hidden_cols": 0,
        "empty": True,
        "table_count": len(getattr(ws, "tables", {}) or {}),
        "table_names": [],
        "last_row_first_cells": [],
        "total_row_suspect": False,
        "error": None,
    }
    try:
        if f["table_count"]:
            f["table_names"] = list(ws.tables.keys())[:20]

        # declared dimension
        try:
            dim = ws.calculate_dimension()
        except Exception:
            dim = ws.dimensions
        f["declared_dim"] = dim
        parsed = parse_dim(dim)
        if parsed:
            f["declared_rows"], f["declared_cols"] = parsed

        # actual used range + formula count
        min_r = min_c = None
        max_r = max_c = 0
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if v is None or (isinstance(v, str) and v.strip() == ""):
                    continue
                f["empty"] = False
                r, c = cell.row, cell.column
                if min_r is None or r < min_r:
                    min_r = r
                if min_c is None or c < min_c:
                    min_c = c
                if r > max_r:
                    max_r = r
                if c > max_c:
                    max_c = c
                if cell.data_type == "f" or (isinstance(v, str) and v.startswith("=")):
                    f["formula_cells"] += 1
        f["actual_rows"] = max_r
        f["actual_cols"] = max_c

        # 幽灵 used range：声明远大于实际（阈值 3 倍且多出 20 行/列以上）
        if f["declared_rows"] and not f["empty"]:
            dr = f["declared_rows"] - f["actual_rows"]
            dc = f["declared_cols"] - f["actual_cols"]
            ratio = f["declared_rows"] / max(f["actual_rows"], 1)
            f["ghost_ratio"] = round(ratio, 2)
            if ratio >= 3 and (dr >= 20 or dc >= 10):
                f["ghost_used_range"] = True

        # merged cells
        merged = list(getattr(ws, "merged_cells", None).ranges) if getattr(ws, "merged_cells", None) else []
        f["merged_count"] = len(merged)

        # 表头候选：顶部连续「全字符串」行（允许单格空），>=2 行视为多行表头嫌疑
        hdr = 0
        for row in ws.iter_rows(min_row=1, max_row=min(30, max(max_r, 1))):
            vals = [c.value for c in row]
            nonempty = [v for v in vals if v is not None and str(v).strip() != ""]
            if not nonempty:
                # 空行不打断表头判定，但仅允许连续 1 个空行
                if hdr:
                    break
                continue
            if all(isinstance(v, str) for v in nonempty):
                hdr += 1
            else:
                break
        f["header_rows"] = hdr
        f["header_multi_row_suspect"] = hdr >= 2

        # 合并单元格是否落在表头区（前 5 行 / 或检测到的表头行数内）
        hz = max(5, hdr)
        for mr in merged:
            if mr.min_row <= hz:
                f["merged_in_header"] += 1

        # 隐藏行/列
        for rd in ws.row_dimensions.values():
            if getattr(rd, "hidden", False):
                f["hidden_rows"] += 1
        for cd in ws.column_dimensions.values():
            if getattr(cd, "hidden", False):
                f["hidden_cols"] += 1

        # 表尾合计行嫌疑
        if max_r:
            grab = []
            for row in ws.iter_rows(min_row=max_r, max_row=max_r):
                grab = [c.value for c in row[:6]]
                break
            f["last_row_first_cells"] = [str(v)[:24] for v in grab if v is not None][:4]
            for v in grab[:3]:
                if isinstance(v, str) and v.strip().lower().startswith(TOTAL_ROW_WORDS):
                    f["total_row_suspect"] = True
                    break
    except Exception as e:  # noqa: BLE001
        f["error"] = f"{type(e).__name__}: {e}"
    return f


def scan_workbook(path: Path) -> dict:
    import openpyxl
    from openpyxl.chartsheet import Chartsheet

    out = {
        "path": str(path),
        "size": path.stat().st_size,
        "ext": path.suffix.lower(),
        "is_lock_file": path.name.startswith(LOCK_PREFIX),
        "ok": False,
        "error": None,
    }
    if out["is_lock_file"]:
        out["error"] = "Excel 锁文件（~$），非真实工作簿"
        return out

    zf = zip_features(path)
    out["zip"] = zf
    if zf.get("error"):
        out["error"] = zf["error"]
        return out

    try:
        wb = openpyxl.load_workbook(path, data_only=False, read_only=False)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"openpyxl 加载失败 {type(e).__name__}: {e}"
        return out

    try:
        out["ok"] = True
        out["sheet_count"] = len(wb.sheetnames)
        out["sheet_names"] = wb.sheetnames[:60]
        try:
            out["defined_names"] = len(wb.defined_names)
        except Exception:
            out["defined_names"] = len(getattr(wb, "defined_names", {}) or {})
        out["external_links_api"] = len(getattr(wb, "_external_links", []) or [])
        chartsheets = [s.title for s in wb.chartsheets] if hasattr(wb, "chartsheets") else []
        out["chartsheet_names"] = chartsheets
        if not chartsheets:
            chartsheets = [s.title for s in wb.worksheets if isinstance(s, Chartsheet)]
            out["chartsheet_names"] = chartsheets

        sheets = []
        for ws in wb.worksheets:
            if isinstance(ws, Chartsheet):
                continue
            sheets.append(sheet_features(ws))
        out["sheets"] = sheets

        # 汇总
        out["hidden_sheet_count"] = sum(1 for s in sheets if s["state"] != "visible")
        out["total_formula_cells"] = sum(s["formula_cells"] for s in sheets)
        out["total_merged"] = sum(s["merged_count"] for s in sheets)
        out["total_merged_in_header"] = sum(s["merged_in_header"] for s in sheets)
        out["multi_row_header_sheets"] = sum(1 for s in sheets if s["header_multi_row_suspect"])
        out["ghost_sheets"] = sum(1 for s in sheets if s["ghost_used_range"])
        out["hidden_rows_total"] = sum(s["hidden_rows"] for s in sheets)
        out["hidden_cols_total"] = sum(s["hidden_cols"] for s in sheets)
        out["total_row_sheets"] = sum(1 for s in sheets if s["total_row_suspect"])
        out["table_total"] = sum(s["table_count"] for s in sheets)
        out["empty_sheets"] = sum(1 for s in sheets if s["empty"])
    finally:
        wb.close()

    return out


def scan_csv(path: Path) -> dict:
    out = {
        "path": str(path), "size": path.stat().st_size, "ext": ".csv",
        "ok": True, "error": None, "is_lock_file": False, "kind": "csv",
    }
    try:
        raw = path.read_bytes()
        enc = sniff_encoding(raw)
        out["encoding"] = enc
        sample = raw[:65536].decode(enc, errors="replace")
        out["delimiter"] = sniff_delimiter(sample)
        lines = sample.splitlines()
        out["line_count_sample"] = len(lines)
        out["field_count_first_line"] = len(next(_csv.reader([lines[0]]))) if lines else 0
        out["has_bom"] = raw[:3] == b"\xef\xbb\xbf"
    except Exception as e:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_dir")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--summary", action="store_true", help="打印人读摘要")
    args = ap.parse_args()

    root = Path(args.corpus_dir)
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    buckets: dict[str, list[Path]] = {}
    all_files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            all_files += 1
            ext = Path(fn).suffix.lower()
            buckets.setdefault(ext, []).append(Path(dirpath) / fn)

    print(f"[scan] 根目录: {root}")
    print(f"[scan] 文件总数: {all_files}")
    print("[scan] 扩展名分布:")
    for ext, files in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        total = sum(f.stat().st_size for f in files)
        print(f"    {ext or '(无扩展名)':<12} {len(files):>5}  {total/1024/1024:>10.2f} MB")

    excel_paths = [p for ext in sorted(OPENPYXL_EXTS | NEED_CONVERT_EXTS | CSV_EXTS)
                   for p in buckets.get(ext, [])]
    lock_paths = [p for p in excel_paths if p.name.startswith(LOCK_PREFIX)]
    real_paths = [p for p in excel_paths if p.name not in {q.name for q in lock_paths} or not p.name.startswith(LOCK_PREFIX)]
    real_paths = [p for p in real_paths if not p.name.startswith(LOCK_PREFIX)]

    print(f"[scan] Excel 类文件: {len(excel_paths)} (含锁文件 {len(lock_paths)})，实扫 {len(real_paths)}")

    results = []
    for p in sorted(real_paths):
        ext = p.suffix.lower()
        if ext in CSV_EXTS:
            r = scan_csv(p)
        elif ext in OPENPYXL_EXTS:
            r = scan_workbook(p)
        else:
            r = {"path": str(p), "size": p.stat().st_size, "ext": ext,
                 "ok": False, "error": "NEED_SOFFICE_CONVERT: openpyxl 不支持，需转换后复扫"}
        results.append(r)
        flag = "OK " if r.get("ok") else "ERR"
        print(f"    [{flag}] {r['size']:>9}B  {r['path'][len(str(root)):]}  {r.get('error') or ''}")

    payload = {
        "root": str(root),
        "total_files_in_root": all_files,
        "extension_distribution": {k or "(none)": len(v) for k, v in buckets.items()},
        "excel_total": len(excel_paths),
        "lock_files": [str(p) for p in lock_paths],
        "scanned": len(results),
        "results": results,
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[scan] JSON -> {args.out}")
    if args.summary:
        for r in results:
            if not r.get("ok"):
                continue
            if r.get("kind") == "csv":
                print(f"\n== {r['path']}  enc={r['encoding']} delim={r['delimiter']!r}")
                continue
            print(f"\n== {Path(r['path']).name}  sheets={r['sheet_count']} hidden={r['hidden_sheet_count']}")
            for s in r["sheets"]:
                print(f"   - {s['name']!r} state={s['state']} dim={s['declared_dim']} "
                      f"actual={s['actual_rows']}x{s['actual_cols']} ghost={s['ghost_used_range']} "
                      f"merged={s['merged_count']}/hdr={s['merged_in_header']} hdrRows={s['header_rows']} "
                      f"fx={s['formula_cells']} hid={s['hidden_rows']}r/{s['hidden_cols']}c "
                      f"tbl={s['table_count']} total={s['total_row_suspect']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
