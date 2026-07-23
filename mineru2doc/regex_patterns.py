"""章节编号正则 + 工具（从 postprocess.py / heading_scorer.py 移植，独立内联）。

覆盖中文公文常见编号：``1``/``1.1``、``1、``/``（1）``、``第X章/节/条/篇/部``、
``一、``、``（一）``。``section_number_depth`` 把编号映射成文档逻辑层级（depth）。
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_NUM = r"[一二三四五六七八九十百千零〇两]+"

# 编号前缀（匹配文本开头）。顺序敏感：数字层级优先于单数字+标点。
SECTION_PREFIX = re.compile(
    r"^("
    r"\d+(?:[.．]\d+)*"               # 1 / 1.1 / 1.2.1（数字层级）
    r"|\d+[、．.]"                     # 1、 / 1． / 1.
    r"|[（(]\d+[）)]"                 # （1）
    r"|第" + _NUM + r"[章节条篇部]"    # 第一章 / 第二节 / 第三条 / 第一篇 / 第一部
    r"|" + _NUM + r"、"                # 一、
    r"|[（(]" + _NUM + r"[）)]"        # （一）
    r")"
)

# "句号后有正文"：编号开头但含完整句子 → 是段落不是标题（迁自 classification）
BODY_AFTER_PUNCT_RE = re.compile(r"[。！？]\s*\S")


def split_number_and_title(text: str) -> Tuple[Optional[str], str]:
    """拆出前导编号与标题正文。

    Returns:
        (编号 or None, 去掉编号并 strip 后的文本)。无编号时 (None, 原文 strip)。

    Examples:
        "3.2.1 项目查看与处理" → ("3.2.1", "项目查看与处理")
        "第一章 集团立项"      → ("第一章", "集团立项")
        "（二）发展基础"       → ("（二）", "发展基础")
        "这是正文"            → (None, "这是正文")
    """
    t = (text or "").strip()
    m = SECTION_PREFIX.match(t)
    if not m:
        return None, t
    num = m.group(1)
    return num.strip(), t[len(num):].strip()


def extract_section_number(text: str) -> Optional[str]:
    """文本前导章节号（无则 None）。"""
    return split_number_and_title(text)[0]


def section_number_depth(section_number: str) -> int:
    """编号的层级深度（无编号/空 → 0）。

    Examples:
        "1" → 1  "1.1" → 2  "1.2.1" → 3
        "1、" / "1．" → 1   "（1）" → 2
        "第一章" / "第一篇" / "第一部" → 1   "第二节" / "第三条" → 2
        "一、" → 1   "（一）" → 2
    """
    if not section_number:
        return 0
    sn = section_number.strip()

    # 数字层级：1 / 1.1（含全角 ．）
    if re.match(r"^\d+(?:[.．]\d+)*$", sn):
        return sn.count(".") + sn.count("．") + 1

    # 单数字 + 顿号/句点：1、 1． 1.
    if re.match(r"^\d+[、．.]$", sn):
        return 1

    # 括号阿拉伯数字：（1）
    if re.match(r"^[（(]\d+[）)]$", sn):
        return 2

    # 第X章/节/条/篇/部
    m = re.match(r"^第" + _NUM + r"([章节条篇部])$", sn)
    if m:
        return 1 if m.group(1) in "章篇部" else 2  # 章/篇/部=1，节/条=2

    # 中文数字 + 顿号：一、
    if re.match(r"^" + _NUM + r"、$", sn):
        return 1

    # 括号中文数字：（一）
    if re.match(r"^[（(]" + _NUM + r"[）)]$", sn):
        return 2

    return 1  # 兜底


def has_body_after_punct(text: str) -> bool:
    """编号开头但 ``。！？`` 后还有正文 → 是带正文的段落，不是纯标题。"""
    return bool(BODY_AFTER_PUNCT_RE.search(text or ""))
