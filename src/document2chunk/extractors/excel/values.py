"""值规范化（Q6/Q14 定稿）：显示值 − 千分位 + ISO 日期 + trim；货币符/百分号保留。"""
from __future__ import annotations

import re
from datetime import date, datetime

from document2chunk.extractors.excel.models import CellFmt

_DEC = re.compile(r"0\.(0+)")
_CUR = re.compile(r"^[^#0,\-\+\s%eE]+")


def _currency_symbol(fmt: str) -> str:
    fmt = (fmt or "").replace('"', "")  # 去引号（Excel 数字格式中货币符常写作 "¥"）
    if not fmt or fmt.lower() == "general":
        return ""
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


ERROR_ZH: dict[str, str] = {
    "#N/A": "无可用值",
    "#DIV/0!": "除零错误",
    "#VALUE!": "值错误",
    "#REF!": "引用失效",
    "#NAME?": "未知名称",
    "#NUM!": "数值错误",
    "#NULL!": "空交集",
}


def error_zh(value: object) -> object:
    """错误字面量 → 中文语义。仅 text 层用；data 层保留原文（Q5 a3，词表终稿见 OI-1）。"""
    if isinstance(value, str):
        return ERROR_ZH.get(value.strip(), value)
    return value
