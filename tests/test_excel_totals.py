"""合计行/小计行信号收集与分层（难点13/N6，Q2 分层打标）。

信号多选：keyword（行首词表）/ strict（全列校验和）/ partial（末带部分命中）/ position（末行佐证）。
分层：definite（from_total）= keyword 且至少一个佐证；candidate（from_total_candidate）=
其余存在非 position 信号者——证据不足不打死，strict-only 一律降级 candidate。
"""
from tests._excel_fixtures import build_xlsx


def _grid(rows):
    from document2chunk.extractors.excel.reader import read_sheet_grids

    grids, _ = read_sheet_grids(build_xlsx({"s": rows}))
    return grids[0]


def _region(grid):
    from document2chunk.extractors.excel.boundary import split_regions

    return split_regions(grid)[0]


def _signals(rows):
    from document2chunk.extractors.excel.totals import collect_total_signals

    g = _grid(rows)
    return collect_total_signals(g, _region(g), 1)


def test_red_keyword_total_row():
    from document2chunk.extractors.excel.totals import classify_total_rows

    # 行首列命中词表 + 末行位置佐证 → definite（from_total）
    signals = _signals([["科目", "金额"], ["人员费", 100], ["设备费", 200], ["合计", 300]])
    assert "keyword" in signals[3]
    definite, candidate = classify_total_rows(signals)
    assert 3 in definite and 3 not in candidate
    assert set(definite[3]) >= {"keyword", "position"}


def test_red_checksum_total_row_without_label():
    from document2chunk.extractors.excel.totals import classify_total_rows

    # 无关键词、算术全吻合 → strict；strict-only 不得打死，降级 candidate（Q2 定稿语义）
    signals = _signals([["科目", "金额"], ["人员费", 100], ["设备费", 200], [None, 300]])
    assert "strict" in signals[3]
    definite, candidate = classify_total_rows(signals)
    assert 3 in candidate and 3 not in definite
    assert set(candidate[3]) >= {"strict", "position"}


def test_red_normal_row_not_marked():
    from document2chunk.extractors.excel.totals import classify_total_rows

    # 普通行：无关键词、算术不符 → 仅末行 position 佐证，两个标都不打
    signals = _signals([["科目", "金额"], ["人员费", 100], ["设备费", 200], ["材料费", 50]])
    assert signals == {3: ["position"]}
    definite, candidate = classify_total_rows(signals)
    assert 3 not in definite and 3 not in candidate


def test_red_column_named_total_not_misleading():
    from document2chunk.extractors.excel.totals import classify_total_rows

    # 词表只匹配行首列：行首是科目名、首列表头恰含"合计金额"的数据行不得误杀
    signals = _signals([["科目", "合计金额"], ["人员费", 100], ["设备费", 200]])
    assert all("keyword" not in ss for ss in signals.values())
    assert 1 not in signals
    assert signals[2] == ["position"]
    definite, candidate = classify_total_rows(signals)
    assert not definite and not candidate
