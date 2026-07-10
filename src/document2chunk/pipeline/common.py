"""span 管线公共工具。

仅保留 9-Stage 与提取前端在用的函数（依据 ``designs/003`` §7）：
- :func:`normalize_font_size`：字号归一化到 0.2pt 网格
- :func:`infer_heading_level` / :func:`infer_heading_level_with_score`：字号比值推断标题层级
- :func:`read_jsonl`：读取 JSONL（版面分析等外部输入）

丢弃：旧 ``common.py`` 的死函数（setup_logging/ensure_dir/format_file_size/
get_project_root）与孤立 write_jsonl。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ==================== 字号归一化 ====================


def normalize_font_size(size: float, step: float = 0.2) -> float:
    """将字号归一化到标准网格（默认步进 0.2pt）。

    PDF 中实际字号常因浮点误差出现微小偏差（如 14.05、14.12），
    通过归一化对齐到标准字号网格，消除这类噪声。

    Examples:
        14.05 → 14.0
        14.15 → 14.2
        10.45 → 10.4
        9.03  → 9.0
    """
    return round(size / step) * step


# ==================== 字号推断标题层级 ====================

# 字号比值 → 置信度评分映射（H1–H4）
_HEADING_SCORE_MAP: dict[int, float] = {
    1: 0.50,  # H1: ≥ 1.6×
    2: 0.45,  # H2: ≥ 1.3×
    3: 0.40,  # H3: ≥ 1.15×
    4: 0.30,  # H4: ≥ 1.05×
}


def infer_heading_level(
    font_size: float,
    body_font_size: float | None = None,
    threshold_multiplier: float = 1.0,
) -> int | None:
    """根据字号推断标题层级（1–4），非标题返回 None。

    针对中文公文/文档优化：中文标题字号（22-28pt）与正文（16pt）差距不大，
    阈值较紧凑。

    示例（正文 16pt 时）：
        H1: ≥ 25.6pt (1.6×)  H2: ≥ 20.8pt (1.3×)
        H3: ≥ 18.4pt (1.15×) H4: ≥ 16.8pt (1.05×)
    """
    level, _ = infer_heading_level_with_score(
        font_size, body_font_size, threshold_multiplier
    )
    return level


def infer_heading_level_with_score(
    font_size: float,
    body_font_size: float | None = None,
    threshold_multiplier: float = 1.0,
) -> tuple[int | None, float]:
    """根据字号推断标题层级并返回置信度分数。

    Returns:
        (level, score)：level 为 1–4 或 None；score 为 0.0–1.0。
    """
    if body_font_size is None:
        body_font_size = font_size

    ratio = font_size / body_font_size if body_font_size > 0 else 1.0

    if ratio >= threshold_multiplier * 1.6:
        return 1, _HEADING_SCORE_MAP[1]
    if ratio >= threshold_multiplier * 1.3:
        return 2, _HEADING_SCORE_MAP[2]
    if ratio >= threshold_multiplier * 1.15:
        return 3, _HEADING_SCORE_MAP[3]
    if ratio >= threshold_multiplier * 1.05:
        return 4, _HEADING_SCORE_MAP[4]
    return None, 0.0


# ==================== JSONL 读取 ====================


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """读取 JSONL 文件为字典列表（跳过空行）。"""
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
