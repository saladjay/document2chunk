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
