"""合计行/小计行识别（轨 A 打标；轨 B 二期据同一函数剔除）。

Q2 分层打标（2026-09-21 grill 定稿）：证据不足不打死。
信号（多选）：
1. keyword  行首列命中词表（只匹配行首列，防"合计金额"列名误杀）——任意位置；
2. strict   行内全部数值列 = 上方非 keyword 行之和（容差相对 1e-6）——任意位置；
3. partial  checked>=2 且 hits>=1，仅评估区域末 _PARTIAL_BAND 行——防清单类大表
            中段随机撞和（13/14/15/20/23 canary 零差异护栏）；
4. position 区域最后一个数据行——只作佐证，永不单独成信号。
分层：definite（from_total）= 有 keyword 且 len(signals)>=2；
     candidate（from_total_candidate）= 其余存在非 position 信号者；position 不单飞。
可调参数 _PARTIAL_BAND 与 partial 阈值待 23 文件重跑后校准（open-issue.md OI-7）。
"""
from __future__ import annotations

from document2chunk.extractors.excel.models import Region, SheetGrid

TOTAL_WORDS = ("合计", "总计", "小计", "汇总", "累计")
_TOTAL_EN = {"total", "subtotal", "grand total"}
_REL_TOL = 1e-6
_PARTIAL_BAND = 3


def _is_total_label(v: object) -> bool:
    if not isinstance(v, str):
        return False
    s = v.strip()
    if not s:
        return False
    return any(s == w or s.startswith(w) for w in TOTAL_WORDS) or s.lower() in _TOTAL_EN


def _numeric(grid: SheetGrid, r: int, c: int) -> object:
    row = grid.values[r] if r < grid.n_rows else []
    v = row[c] if c < len(row) else None
    if isinstance(v, bool):
        return None
    return v if isinstance(v, (int, float)) else None


def collect_total_signals(
    grid: SheetGrid, region: Region, header_rows: int
) -> dict[int, list[str]]:
    """每数据行 → 命中信号列表（keyword/strict/partial/position 任意组合）。

    信号一律在 region 列窗 [c1, c2] 内取值：同物理行并排的侧表（05 号决算表）
    不得被主表列稀释——keyword 不得读到窗外行首、数值列不得并入窗外列。
    """

    def _window_cells(r: int) -> list[tuple[int, object]]:
        row = grid.values[r] if r < grid.n_rows else []
        out: list[tuple[int, object]] = []
        for c in range(region.c1, region.c2 + 1):
            v = row[c] if c < len(row) else None
            if v is not None and not (isinstance(v, str) and v.strip() == ""):
                out.append((c, v))
        return out

    data_rows = list(range(region.r1 + header_rows, region.r2 + 1))
    signals: dict[int, list[str]] = {}
    if not data_rows:
        return signals

    keyword_rows: set[int] = set()
    for r in data_rows:
        cells = _window_cells(r)
        if cells and _is_total_label(cells[0][1]):
            keyword_rows.add(r)
            signals.setdefault(r, []).append("keyword")

    numeric_cols = sorted(
        {
            c
            for r in data_rows
            for c, v in _window_cells(r)
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
    )
    band_start = data_rows[-min(_PARTIAL_BAND, len(data_rows))]
    for r in data_rows:
        # keyword 行也参与 strict/partial 评估：definite 需 len(signals)>=2，
        # 侧表合计（05 号）往往窗内仅 1 数值列、唯一佐证就是 strict——跳过则永居 candidate。
        checked = hits = 0
        for c in numeric_cols:
            v = _numeric(grid, r, c)
            if v is None:
                continue
            col_sum = sum(
                s
                for rr in data_rows
                if rr < r and rr not in keyword_rows and (s := _numeric(grid, rr, c)) is not None
            )
            checked += 1
            if abs(v - col_sum) <= _REL_TOL * max(1.0, abs(col_sum)):
                hits += 1
        if checked == 0:
            continue
        if hits == checked:
            signals.setdefault(r, []).append("strict")
        elif checked >= 2 and hits >= 1 and r >= band_start:
            signals.setdefault(r, []).append("partial")
    signals.setdefault(data_rows[-1], []).append("position")
    return signals


def classify_total_rows(
    signals: dict[int, list[str]],
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    """→ (definite: 打 from_total, candidate: 打 from_total_candidate + warning)。"""
    definite: dict[int, list[str]] = {}
    candidate: dict[int, list[str]] = {}
    for r, ss in signals.items():
        if "keyword" in ss:
            if len(ss) >= 2:
                definite[r] = ss
            else:
                candidate[r] = ss
        elif any(s != "position" for s in ss):
            candidate[r] = ss
    return definite, candidate
