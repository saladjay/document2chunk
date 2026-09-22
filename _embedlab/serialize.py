# -*- coding: utf-8 -*-
"""表格嵌入格式实验 · Step4a 六变体序列化器（纯函数，输入 material 表格 dict）。

V1 现状 HTML（复刻 export/_helpers.html_table_markdown 行为）
V2 展开管道表（span 复制填充 → 规则网格 → pipe）
V3 KV 平铺（轨A 式「表头路径: 值」）
V4 XML（带行/列坐标）
V5 HTML+前置标题行
V6 NL 摘要（外部生成，见 gen_queries.py）
"""
from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def esc_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def esc_pipe(text: str) -> str:
    return text.replace("|", "\\|")


def esc_xml(text: str) -> str:
    return esc_html(text).replace('"', "&quot;")


# ---------- 网格展开（V2/V3/V4 共用） ----------

def expand_grid(table: dict) -> tuple[list[list[str]], int]:
    """span 复制填充 → grid[text]；返回 (grid, ncols)。原点放文本，覆盖区占位。"""
    rows = table["rows"]
    ncols = 0
    for row in rows:
        ncols = max(ncols, sum(max(1, c.get("c", 1)) for c in row["cells"]))
    grid: list[list[str | None]] = [[None] * ncols for _ in rows]
    for ri, row in enumerate(rows):
        col = 0
        for cell in row["cells"]:
            cs = max(1, cell.get("c", 1))
            rs = max(1, cell.get("r", 1))
            while col < ncols and grid[ri][col] is not None:
                col += 1
            text = norm(cell.get("t", ""))
            for dr in range(rs):
                for dc in range(cs):
                    if ri + dr < len(rows) and col + dc < ncols:
                        if dr == 0 and dc == 0:
                            grid[ri][col] = text
                        elif grid[ri + dr][col + dc] is None:
                            # 合并覆盖区：restart 有值则复制，续格空则留空
                            grid[ri + dr][col + dc] = text if text else ""
            col += cs
    return [[(v if v is not None else "") for v in r] for r in grid], ncols


# ---------- V1 / V5 ----------

def v1_html(table: dict) -> str:
    out = ["<table>"]
    for row in table["rows"]:
        out.append("<tr>")
        for cell in row["cells"]:
            tag = "th" if row.get("is_header") else "td"
            attrs = ""
            if cell.get("c", 1) > 1:
                attrs += f' colspan="{cell["c"]}"'
            if cell.get("r", 1) > 1:
                attrs += f' rowspan="{cell["r"]}"'
            out.append(f"<{tag}{attrs}>{esc_html(norm(cell.get('t', '')))}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "\n".join(out)


def v5_caption(table: dict, title: str, caption: str) -> str:
    sign = caption or title or "表格"
    return f"表格：{norm(sign)}\n" + v1_html(table)


# ---------- V2 ----------

def v2_pipe_expanded(table: dict) -> str:
    grid, ncols = expand_grid(table)
    if not ncols:
        return ""
    lines = []
    for i, row in enumerate(grid):
        lines.append("| " + " | ".join(esc_pipe(v) or " " for v in row) + " |")
        if i == 0:  # 与管线 table_markdown 同约定：首行后置分隔行
            lines.append("| " + " | ".join("---" for _ in range(ncols)) + " |")
    return "\n".join(lines)


# ---------- V3 ----------

def _header_keys(table: dict, grid: list[list[str]], ncols: int) -> list[str]:
    rows = table["rows"]
    n_head = 0
    for row in rows:
        if row.get("is_header"):
            n_head += 1
        else:
            break
    if n_head == 0 and len(rows) > 1 and all(v for v in grid[0]):
        n_head = 1  # 无标记时首行全非空则视作表头（与轨A 保守口径一致）
    keys: list[str] = []
    for c in range(ncols):
        parts: list[str] = []
        for r in range(n_head):
            v = grid[r][c].strip()
            if v and v not in parts:  # 空层跳过、层内去重
                parts.append(v)
        keys.append("-".join(parts) or f"列{c + 1}")
    # 撞名加（2）（3）…（与轨A flatten 同约定）
    seen: dict[str, int] = {}
    for i, k in enumerate(keys):
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            keys[i] = f"{k}（{seen[k]}）"
    return keys


def v3_kv(table: dict) -> str:
    grid, ncols = expand_grid(table)
    if not ncols:
        return ""
    keys = _header_keys(table, grid, ncols)
    n_head = sum(1 for row in table["rows"] if row.get("is_header")) or (
        1 if len(table["rows"]) > 1 and all(v for v in grid[0]) else 0
    )
    lines = []
    for r in range(n_head, len(grid)):
        pairs = [
            f"{keys[c]}: {grid[r][c]}"
            for c in range(ncols)
            if grid[r][c].strip()
        ]
        if pairs:
            lines.append("; ".join(pairs))
    return "\n".join(lines)


# ---------- V4 ----------

def v4_xml(table: dict) -> str:
    grid, ncols = expand_grid(table)
    if not ncols:
        return ""
    out = ["<table>"]
    for ri, row in enumerate(table["rows"]):
        out.append(f'<row r="{ri + 1}">')
        col = 0
        for cell in row["cells"]:
            cs = max(1, cell.get("c", 1))
            rs = max(1, cell.get("r", 1))
            attrs = f' c="{col + 1}"'
            if cs > 1:
                attrs += f' colspan="{cs}"'
            if rs > 1:
                attrs += f' rowspan="{rs}"'
            out.append(f"<cell{attrs}>{esc_xml(norm(cell.get('t', '')))}</cell>")
            col += cs
        out.append("</row>")
    out.append("</table>")
    return "\n".join(out)


VARIANTS = ["v1_html", "v2_pipe", "v3_kv", "v4_xml", "v5_caption", "v6_summary"]


def serialize(variant: str, table: dict, title: str = "", caption: str = "",
              summary: str = "") -> str:
    if variant == "v1_html":
        return v1_html(table)
    if variant == "v2_pipe":
        return v2_pipe_expanded(table)
    if variant == "v3_kv":
        return v3_kv(table)
    if variant == "v4_xml":
        return v4_xml(table)
    if variant == "v5_caption":
        return v5_caption(table, title, caption)
    if variant == "v6_summary":
        if not summary:
            raise ValueError("v6_summary 需要先生成摘要（gen_queries.py 产物）")
        return summary
    raise KeyError(variant)
