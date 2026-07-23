"""标题判定器（接缝）。

- :class:`RegexJudge`（现在）：MinerU 为主，正则补救。
- 未来：替换为多信号或模型判定器，实现同一 ``remediate`` 接口即可。

补救规则（spec §5，2026-07-23 改"相对定级"）：
- 编号标题（① 修错级 / ② 补漏检）：**不绝对覆盖层级**，只在块上标 ``number_depth``
  （编号的相对深度），交给 :func:`normalize.normalize_levels` 按栈序相对定级。
  原因：绝对 depth（一、=H1）忽略编号方案的起始层级，实测把公文正文一/二/三
  顶到与文档大标题同级（51 H1 扁平）。相对栈序让"首见编号样式落位到上下文层级、
  更深的自动嵌套"。
- ③ 降误检（默认关）：MinerU 判标题 + 无编号 + 文本 > demote_min_len 且以句末符结尾 → 降正文。

只决定"是不是标题 + 编号深度"，不决定绝对层级（那是 normalize 的事）。
"""

from __future__ import annotations

from typing import List, Protocol

from .model import TEXT, Block
from .regex_patterns import (
    has_body_after_punct,
    section_number_depth,
    split_number_and_title,
)

_SENTENCE_END = "。！？"


class TitleJudge(Protocol):
    """标题判定器接口：在 Block 列表上就地做补救决策。"""

    def remediate(self, blocks: List[Block]) -> List[Block]: ...


class RegexJudge:
    """正则补救式标题判定器（简化版）。"""

    def __init__(
        self,
        *,
        demote: bool = False,
        max_title_len: int = 40,
        demote_min_len: int = 60,
    ) -> None:
        self.demote = demote
        self.max_title_len = max_title_len
        self.demote_min_len = demote_min_len

    def remediate(self, blocks: List[Block]) -> List[Block]:
        return [self._remediate_one(b) for b in blocks]

    # ── 单块决策 ──

    def _remediate_one(self, b: Block) -> Block:
        if b.type != TEXT:
            return b
        text = (b.text or "").strip()
        if not text:
            return b

        num, title_part = split_number_and_title(text)
        mineru_heading = b.level is not None  # MinerU 标了 text_level

        # ① 修错级 / ② 补漏检：编号标题 → 标 number_depth（相对，交 normalize）
        if num is not None:
            depth = section_number_depth(num)
            if depth <= 0:
                return b
            if mineru_heading:
                # ① MinerU 已判标题 + 编号：标 depth，层级由 normalize 相对重定
                b.number_depth = depth
                return b
            # ② MinerU 正文 + 编号：可提升才标（保守：短 + 无句尾正文）
            if self._promotable(title_part, text):
                b.number_depth = depth
                b.level = depth  # 占位（标记为标题）；normalize 按 number_depth 相对重定
            return b

        # 无编号：
        if mineru_heading:
            # ③ 降误检（默认关）
            if self.demote and len(text) > self.demote_min_len and text[-1] in _SENTENCE_END:
                b.level = None
            return b
        return b  # 无编号正文

    def _promotable(self, title_part: str, text: str) -> bool:
        """正文 → 标题 的保守判定：有标题文 + 短 + 无句尾正文。"""
        return (
            len(title_part) > 0
            and len(title_part) <= self.max_title_len
            and not has_body_after_punct(text)
        )
