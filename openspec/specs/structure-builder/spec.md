# structure-builder — 章节树构建行为契约

> 依赖：`document2chunk.ir`
> IR 定义：`designs/001-target-architecture.md` §4
> 所有 extractor 产出 `content`（已判定 heading level）后，交由本模块构建章节树。

## 1. 职责

从 `content`（块序列，`HeadingNode.level` 已由 extractor 判定）构建：
- `section_tree`：嵌套章节树（根 `level=0`）。
- `block_to_section`：`block_id → section_id` 映射。
- 可选 `TocNode`：当 extractor 提供 TOC 条目且 `keep_toc=True` 时聚合导出。

本模块是 `section_tree` / `block_to_section` 的**唯一生产者**（见 ir-model spec §2.1）。

## 2. 需求

- **必须**：用单遍栈算法构建 `section_tree`，时间 O(n)、空间 O(d)。
- **必须**：层级规则——`HeadingNode(level=N)` 是栈中 `level < N` 的最近章节的子章节；正文块归属当前栈顶章节。
- **必须**：根 `SectionNode` 为 `level=0`、`title="ROOT"`、`id="sec_root"`。
- **必须**：每个 `SectionNode` 记录 `heading_node_id`（对应 HeadingNode）、`block_ids`（下属块，按序）、`subsections`（嵌套子章节）、`parent_id`。
- **必须**：`block_to_section` 与 `section_tree.block_ids` 完全一致。
- **必须**：接受可选 `toc_map: dict[text→level]`，在构建前用它校准 `HeadingNode.level`（TOC 信号消费）。
- **必须**：`keep_toc=True` 且 extractor 提供 TOC 条目时，产出单个 `TocNode`（`entries=[{text, level, page?}]`）并按 extractor 指示决定是否纳入 `content`。
- **禁止**：在 `keep_toc=False`（默认）时向 `content` 注入 `TocNode`。

### 边界处理

| 条件 | 处理 |
|---|---|
| 文档无标题 | 所有块归 `sec_root` |
| 层级跳跃（H1→H3） | 新章节挂到栈中 `level < 3` 的最近节点 |
| 连续标题无正文 | 正常建树，子树可为空 |
| `level > 9` | 截断到 9 |
| `level < 1`（异常） | 视为正文，不建章节 |

## 3. 算法（栈，伪码）

```python
def build(content, toc_map=None, toc_entries=None, keep_toc=False):
    if toc_map:
        for b in content:
            if isinstance(b, HeadingNode) and b.text in toc_map:
                b.level = toc_map[b.text]

    root = SectionNode(id="sec_root", title="ROOT", level=0)
    stack = [root]
    block_to_section = {}

    for block in content:
        if isinstance(block, HeadingNode):
            level = min(max(block.level, 1), 9)
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            sec = SectionNode(id=next_sec_id(), title=block.text, level=level,
                              heading_node_id=block.id, parent_id=stack[-1].id)
            stack[-1].subsections.append(sec)
            stack.append(sec)
        elif isinstance(block, TocNode):
            continue  # 不参与建树
        else:
            cur = stack[-1]
            cur.block_ids.append(block.id)
            block_to_section[block.id] = cur.id

    return root, block_to_section, (toc_node if keep_toc and toc_entries else None)
```

## 4. 场景（When / Then）

- **当** `content = [H1(L1), P1, P2, H2(L2), P3]` **那么** 章节树为 `root → H1{P1,P2, H2{P3}}`。
- **当** 文档无任何 `HeadingNode` **那么** `section_tree` 仅 `sec_root`，所有块在其 `block_ids`。
- **当** `toc_map={"第一章":1}` 且某 HeadingNode.text=="第一章" 但 level 误判为 2 **那么** 校准为 level=1 后建树。
- **当** `keep_toc=False` **那么** 返回的 `content` 中无 `TocNode`。
- **当** `keep_toc=True` 且有 `toc_entries` **那么** 产出 `TocNode(entries=[...])`。

## 5. 涉及实体

`SectionNode`、`HeadingNode`、`TocNode`、`LogicalDocument`（section_tree / block_to_section）。
