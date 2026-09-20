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
