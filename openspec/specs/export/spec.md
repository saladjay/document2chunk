# export — 导出器行为契约

> 实现方：Claude
> 依赖：`document2chunk.ir`
> IR 定义：`designs/001-target-architecture.md` §4

## 1. 职责

把 `LogicalDocument` 导出为多种下游格式。**规范输出是 JSON（AST）**，其余为派生/兼容导出。

## 2. 导出器

| 函数 | 格式 | 定位 |
|---|---|---|
| `to_json(doc, pretty=True)` | LogicalDocument JSON（exclude_none） | **规范输出** |
| `to_markdown(doc, include_metadata=False)` | Markdown | 人类可读 / LLM 上下文 |
| `to_plain_text(doc)` | 纯文本（阅读顺序） | 极简下游 |
| `to_jsonl(doc)` | JSONL（按块/页分行） | 兼容旧 PDF 接口（非规范） |

## 3. 需求

### 3.1 to_json（规范）

- **必须**：`doc.model_dump_json(exclude_none=True)`（pretty 时 indent=2）。
- **必须**：可被 `LogicalDocument.model_validate_json` 无损往返。

### 3.2 to_markdown

- **必须**：遍历 `section_tree`（嵌套）；`SectionNode.level=N`（1–6）→ `"#" * N`；`level>6` → `######`。
- **必须**：段落 → 文本；表格 → 管道表格（`| a | b |`，首行后加分隔行）；列表 → `- `/`1. `（多级缩进）；图片 → `![alt](image_id)`；公式 → `` `latex` `` 或文本。
- **必须**：跳过 `page_number`、（默认）`toc` 节点。
- **必须**：`include_metadata=True` 时文首加 YAML front matter（title/author/source_file）。
- **必须**：按 `block_ids` 顺序输出章节内块，按 `subsections` 递归子章节。

### 3.3 to_plain_text

- **必须**：按 `content` 阅读顺序拼接；表格按行 `\t` 连接；忽略结构标记。

### 3.4 to_jsonl（兼容）

- **必须**：每行一个 JSON 对象（按块或按页，可配置）。
- **禁止**：作为规范契约；仅供旧 PDF 消费方过渡。

## 4. 场景（When / Then）

- **当** `doc` 经 `to_json` → `LogicalDocument.model_validate_json` **那么** 类型与字段完全一致。
- **当** `HeadingNode(level=3, text="节")` **那么** Markdown 输出 `### 节`。
- **当** `TableNode` 2×2 **那么** Markdown 输出含表头分隔行的管道表格。
- **当** `include_metadata=False`（默认） **那么** Markdown 无 front matter。

## 5. 涉及实体

`LogicalDocument`、`SectionNode`、各 `BlockNode`/`InlineNode`、`DocumentMetadata`。
