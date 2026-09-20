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
