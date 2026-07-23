"""相对栈式定级（spec §6，2026-07-23 改"相对"）。

编号标题（带 ``number_depth``）按**相对深度**定级，无编号标题信任 MinerU 层级：

- 维护 ``depth_to_level``：首见的编号深度落位到 ``prev_level + 1``，同深度复用、
  更深的嵌套、回到浅深度则回到其已落位的层级。
- 无编号标题（MinerU 标题）信任其层级（夹防跳级 ``min(level, prev+1)``）；
  当它**回到上级**（``lvl < prev``）说明新章节开始 → 清空 ``depth_to_level``（编号方案重启）。
- 首个标题恒为 H1（``prev`` 从 0 起）。

这样公文里"通知标题(H1) 之下的 一/二/三"会落到 H2，而不是被绝对 depth 顶到 H1。
"""

from __future__ import annotations

from typing import Dict, List

from .model import TEXT, Block


def normalize_levels(blocks: List[Block]) -> List[Block]:
    """就地规整标题层级（相对栈式），返回同一列表。"""
    depth_to_level: Dict[int, int] = {}
    prev = 0
    for b in blocks:
        if not (b.type == TEXT and b.level is not None):
            continue

        if b.number_depth:  # 编号标题：相对定级
            d = b.number_depth
            if d in depth_to_level:
                lvl = depth_to_level[d]
            else:
                lvl = min(prev + 1, 9)
                depth_to_level[d] = lvl
        else:  # 无编号 MinerU 标题：信任层级 + 防跳级
            lvl = min(b.level, prev + 1)
            if lvl < prev:
                depth_to_level = {}  # 回到上级 → 新章节，编号方案重启

        b.level = max(1, min(lvl, 9))
        prev = b.level
    return blocks
