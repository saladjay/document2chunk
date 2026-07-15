"""Heading 置信度评分工具。

提供 heading level 判定所需的公共工具函数和评分累加器（迁移自
``doc-paddle-ocr/pdf_parsers/pipeline/heading_scorer.py``，依据 ``designs/003`` §5）：

- :func:`is_standalone_line`：独立一行判定（AutoLevel 用）
- :func:`extract_section_number` / :func:`section_number_depth` /
  :func:`is_pure_section_number`：章节号解析（AutoLevel + TOCAnalysis 用）
- :class:`HeadingScoreAccumulator`：评分累加器（classification/toc_analysis/auto_level 共用）
"""

from __future__ import annotations

import re
from typing import Optional


# ==================== 独立一行判定 ====================

# 同行判定 Y 坐标容差（pt）
_SAME_LINE_TOLERANCE = 3.0


def is_standalone_line(
    elem: dict,
    all_elements: list[dict],
    page_width: float,
    page_height: float,
    width_ratio_threshold: float = 0.65,
) -> bool:
    """判断元素是否"独立一行"。

    独立一行的条件：
    1. 同行无其他元素：在同一 y 坐标带（±3pt）内没有其他元素
    2. 宽度占比较小：元素宽度 < 页面宽度的 width_ratio_threshold
    3. 位置合理：不在页面顶部 8% 或底部 8%（排除页眉页脚）

    Args:
        elem: 当前元素（需要有 bbox 字段）
        all_elements: 当前页所有元素
        page_width: 页面宽度
        page_height: 页面高度
        width_ratio_threshold: 宽度占比阈值（默认 65%）
    """
    bbox = elem.get("bbox")
    if not bbox or len(bbox) < 4:
        return False

    y0, y1 = bbox[1], bbox[3]
    x0, x1 = bbox[0], bbox[2]
    elem_width = x1 - x0
    elem_center_y = (y0 + y1) / 2

    # 条件 3：位置合理（不在页眉页脚区域）
    if page_height > 0:
        if elem_center_y < page_height * 0.08 or elem_center_y > page_height * 0.92:
            return False

    # 条件 2：宽度占比较小
    if page_width > 0 and elem_width > page_width * width_ratio_threshold:
        return False

    # 条件 1：同行无其他元素
    for other in all_elements:
        if other is elem:
            continue
        other_bbox = other.get("bbox")
        if not other_bbox or len(other_bbox) < 4:
            continue

        other_y0, other_y1 = other_bbox[1], other_bbox[3]
        other_center_y = (other_y0 + other_y1) / 2

        # 同行判定：y 坐标中心点差 ≤ 容差
        if abs(elem_center_y - other_center_y) <= _SAME_LINE_TOLERANCE:
            return False

    return True


# ==================== 章节号提取与深度 ====================

# 章节号正则（匹配文本前导的章节编号部分）
_SECTION_NUM_PREFIX_RE = re.compile(
    r"^("
    r"(\d+(?:\.\d+)*)"  # 分组2: 数字层级
    r"|(第[一二三四五六七八九十百千]+[章节条篇部])"  # 分组3: 第X章/节
    r"|([一二三四五六七八九十]+、)"  # 分组4: 中文数字+顿号
    r"|([（(][一二三四五六七八九十]+[）)])"  # 分组5: 括号中文数字
    r")"
)


def extract_section_number(text: str) -> Optional[str]:
    """从文本中提取前导章节号。

    Examples:
        "3.2.1 项目查看与处理" → "3.2.1"
        "第一章 集团项目立项" → "第一章"
        "一、现状与形势" → "一、"
        "（二）发展基础" → "（二）"
        "这是正文没有章节号" → None
    """
    if not text:
        return None

    text = text.strip()
    m = _SECTION_NUM_PREFIX_RE.match(text)
    if m:
        return m.group(1).strip()
    return None


def section_number_depth(section_number: str) -> int:
    """计算章节号的层级深度。

    Examples:
        "1" → 1  "1.1" → 2  "1.2.1" → 3
        "第一章" → 1  "第二节" → 2  "一、" → 1  "（一）" → 2
    """
    if not section_number:
        return 0

    # 数字层级：按点号分隔的段数
    if re.match(r"^\d+(\.\d+)*$", section_number):
        return len(section_number.split("."))

    # 第X章/节/条
    m = re.match(r"^第[一二三四五六七八九十百千]+([章节条篇部])$", section_number)
    if m:
        unit = m.group(1)
        # 章=1级, 节/条=2级
        if unit == "章":
            return 1
        return 2

    # 中文数字+顿号（一、, 二、）→ 通常是一级
    if re.match(r"^[一二三四五六七八九十]+、$", section_number):
        return 1

    # 括号中文数字（（一）, (二)）→ 通常是二级
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]$", section_number):
        return 2

    return 1  # 默认


def is_pure_section_number(text: str) -> bool:
    """判断文本是否为纯章节号（无标题文本）。

    Examples:
        "3.2.1" → True  "一、" → True  "（一）" → True
        "3.2.1 项目查看" → False  "第一章 集团项目" → False
    """
    if not text:
        return False
    text = text.strip()

    if re.match(r"^\d+(\.\d+)+$", text):
        return True
    if re.match(r"^[一二三四五六七八九十]+、$", text):
        return True
    if re.match(r"^[（(][一二三四五六七八九十]+[）)]$", text):
        return True

    return False


# ==================== 评分累加器 ====================


class HeadingScoreAccumulator:
    """Heading 置信度评分累加器。

    管理 heading_confidence 分数叠加和 heading_level_conf_history 记录。
    分数归一化到 0.0 ~ 1.0，直接叠加，上限 1.0。
    """

    def __init__(self, elem: dict):
        """从元素初始化，读取已有的 confidence 和 history。"""
        self._confidence: float = elem.get("heading_confidence", 0.0) or 0.0
        self._history: list[dict] = list(
            elem.get("heading_level_conf_history", []) or []
        )

    @property
    def confidence(self) -> float:
        return self._confidence

    @property
    def history(self) -> list[dict]:
        return self._history

    def add_score(
        self,
        stage: str,
        rule: str,
        score: float,
        action: str = "boost",
        note: str = "",
    ) -> None:
        """添加评分。

        Args:
            stage: 来源 stage 名称
            rule: 规则名称
            score: 本次得分（0.0 ~ 1.0）
            action: assign / boost / override / skip
            note: 附加说明
        """
        entry: dict = {
            "stage": stage,
            "rule": rule,
            "score": score,
            "action": action,
        }
        if note:
            entry["note"] = note

        self._history.append(entry)

        if action != "skip":
            self._confidence = min(self._confidence + score, 1.0)

    def skip(self, stage: str, rule: str, note: str = "") -> None:
        """记录跳过。"""
        self.add_score(stage, rule, 0.0, action="skip", note=note)

    def apply_to(self, elem: dict) -> None:
        """将评分结果写入元素。"""
        elem["heading_confidence"] = round(self._confidence, 4)
        elem["heading_level_conf_history"] = list(self._history)
