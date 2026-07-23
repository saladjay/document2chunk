"""极简本地 Block 模型（独立于 src/document2chunk，零三方依赖）。

对齐 MinerU ``content_list.json`` 的 block 形态：
``{type, text, text_level?, page_idx?, bbox?}`` + 非文本类型负载。

约定（关键）：
- ``type == TEXT`` 的块，``level is None`` = 正文（MinerU 未标标题）；
  ``level is int`` = 标题层级（MinerU 标了 ``text_level``，或被 title_judge 提升）。
  因此 title_judge.remediate() 入口处 ``level is not None`` 即"MinerU 认为它是标题"。
- 非文本类型（table/image/equation/list）的 ``level`` 恒为 None。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# block 类型（对齐 MinerU content_list）
TEXT = "text"
TABLE = "table"
IMAGE = "image"
EQUATION = "equation"
LIST_T = "list"


@dataclass
class Block:
    """单个内容块。"""

    type: str
    text: str = ""
    # text 块：None=正文，int=标题层级（1..9）。非文本类型恒 None。
    level: Optional[int] = None
    page_idx: Optional[int] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1]

    # 非文本类型的负载（按 type 取用，render 里 switch）
    table_body: Optional[str] = None     # table: HTML/markdown 片段
    img_path: Optional[str] = None       # image: 相对/绝对路径
    caption: Optional[str] = None        # image/table 附带说明
    latex: Optional[str] = None          # equation
    items: Optional[List[str]] = None    # list: 条目纯文本
    ordered: bool = False                # list: 有序/无序
    number_depth: Optional[int] = None   # 标题：编号相对深度（title_judge→normalize 交接）

    @property
    def is_heading(self) -> bool:
        """是否为标题（仅 text 块且 level 非空）。"""
        return self.type == TEXT and self.level is not None


@dataclass
class MinerUDoc:
    """一次 MinerU 解析结果的归一形态。"""

    blocks: List[Block] = field(default_factory=list)
    images_dir: Optional[str] = None  # 图片所在目录（render 引用 img_path 时用）
