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
    import document2chunk.format_detect as fd
    monkeypatch.setattr(
        fd,
        "identify",
        lambda data: SimpleNamespace(kind=FileKind.XLS, ext=".xls"),
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


def test_guard_tilde_lock_file_rejected():
    """~$ Excel 锁文件（约 165B 垃圾）在解析入口必须被拒（定档 N2 守卫）。"""
    import pytest

    from document2chunk.exceptions import UnsupportedFormatError
    from document2chunk import serve

    with pytest.raises(UnsupportedFormatError):
        serve.parse_excel_to_envelope(b"\x20\x00garbage-lock-bytes", "~$a.xlsx")
