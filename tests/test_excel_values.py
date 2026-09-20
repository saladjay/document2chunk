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
