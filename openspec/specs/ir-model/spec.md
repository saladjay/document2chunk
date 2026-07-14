# ir-model — 规范 IR 行为契约

> 设计依据：`designs/001-target-architecture.md` §4
> 实现位置：`src/document2chunk/ir/`（pydantic v2）
> 冒烟测试：`tests/test_ir_smoke.py`

## 1. 职责

定义所有格式 extractor 的**统一输出契约**：类型化文档树 `LogicalDocument`。本模块**零业务依赖**（仅 pydantic），是全局最稳定的叶子模块。

## 2. 需求

### 2.1 文档结构

- **必须**：`LogicalDocument` 包含 `metadata`、`content`、`section_tree`、`block_to_section` 四个字段。
- **必须**：`content` 为扁平阅读序列（`List[BlockNode]`），顺序与源阅读顺序一致。
- **必须**：`section_tree` 为嵌套自包含的章节树（根 `level=0`）。
- **必须**：`block_to_section` 提供 `block_id → section_id` 映射，且与 `section_tree` 一致。
- **禁止**：除 `structure-builder` 外，任何模块旁路修改 `section_tree` / `block_to_section`（保证双视图一致）。

### 2.2 节点类型与判别

- **必须**：每个块/行内节点携带 `type` Literal 字段作为判别联合的 discriminator。
- **必须**：块类型集合 = `{heading, paragraph, table, list, image, formula, toc}`。
- **必须**：行内类型集合 = `{run, hyperlink}`。
- **必须**：`toc` 节点仅在 `keep_toc=True` 时出现在 `content` 中；默认不输出。
- **禁止**：引入独立的 `span` 节点类型——**span 必须映射为 `RunNode`**（PDF span、docx `<w:r>` 统一）。

### 2.3 标题

- **必须**：`HeadingNode.level` 取值 1–9。
- **必须**：docx 由 `outlineLvl`(0–8) 映射为 level(1–9)；PDF 由启发式给出（可靠到 1–4）。
- **禁止**：把任意来源的标题层级全局压平到 4 级。

### 2.4 出处（provenance）

- **必须**：`provenance` 为 `Optional`。
- **必须**：PDF 节点携带 `source_type`、`page_index`、`bbox`；OCR 节点 `source_type="ocr"`，`page_index`/`bbox`/`confidence` 视远程服务 `layoutParsingResults` 而定（可选，见 designs/001 D11）。
- **禁止**：docx 节点携带 `bbox` / `page_index`（designs/001 D6：docx 版面信息退出范围）。
- **必须**：`bbox` 为 `[x0, y0, x1, y1]` 四浮点数；`confidence` 取值 0.0–1.0。

### 2.5 嵌套

- **必须**：`TableCellNode.blocks` 与 `ListItemNode.blocks` 支持**任意 `BlockNode` 嵌套**（含子表格、子列表）。
- **必须**：`SectionNode.subsections` 为嵌套子章节（自包含，非 ID 引用）。

### 2.6 序列化

- **必须**：规范输出为 `LogicalDocument` 的 JSON（`model_dump_json(exclude_none=True)`）。
- **必须**：JSON 可无损往返（`model_validate_json` 还原后类型与字段一致）。
- **必须**：节点 ID 单文档内唯一且稳定，格式 `block_/sec_/run_` + 6 位数字（如 `block_000001`）。

## 3. 场景（When / Then）

- **当** 解析 PDF 得到 span `(text, font, size, bbox=[x0,y0,x1,y1], page=0)` **那么** 产出 `RunNode(text, style=RunProperties(font,size), provenance=Provenance(source_type="pdf", page_index=0, bbox=[...]))`。
- **当** 解析 docx 段落 `<w:p>` **那么** 产出 `ParagraphNode(runs=[RunNode,...])`，且 `provenance=None`。
- **当** docx 段落带 `outlineLvl=2` **那么** 产出 `HeadingNode(level=3)`。
- **当** 对 `LogicalDocument` 做 `model_dump_json` → `model_validate_json` 往返 **那么** 每个节点的 `type` 判别正确还原为对应子类。
- **当** 调用 `doc.get_block(id)` 且该块位于表格单元格内 **那么** 必须能通过深度遍历返回该块。
- **当** 调用 `doc.get_section(id)` **那么** 必须沿 `subsections` 嵌套树返回对应章节。
- **当** `keep_toc=False`（默认） **那么** `content` 中**禁止**出现 `type="toc"` 节点。

## 4. 涉及实体（节点类型）

| 节点 | 关键字段 |
|---|---|
| `LogicalDocument` | metadata, content[], section_tree, block_to_section |
| `DocumentMetadata` | title, author, source_type, source_file, created, modified, page_count, generator, custom |
| `SectionNode` | id, title, level(0–9), heading_node_id, block_ids[], subsections[], parent_id |
| `HeadingNode` | level(1–9), text, runs[] |
| `ParagraphNode` | runs[]（RunNode/HyperlinkNode）, text |
| `TableNode` → `TableRowNode` → `TableCellNode` | cell.blocks[], colspan, rowspan, is_header |
| `ListNode` → `ListItemNode` | ordered, items[], level |
| `ImageNode` | image_id, format, width_emu, height_emu, alt, data? |
| `FormulaNode` | latex?, text? |
| `TocNode` | entries[]（可选导出） |
| `RunNode` | text, style(RunProperties), provenance? |
| `HyperlinkNode` | target, runs[] |
| `Provenance` | source_type, page_index?, bbox?, confidence? |
| `RunProperties` | font, font_size, bold, italic, underline, strikethrough, color, highlight, is_super/subscript |
| `ExtractionResult` | content[]（已判定 level 的块）, metadata, toc_entries? — **extractor → structure-builder 握手类型**（加性扩展） |
| `TocEntry` | text, level?, page? — 目录条目（信号/可选导出） |

## 5. 涉及枚举

| 枚举 | 值 |
|---|---|
| `SourceType` | pdf, ocr, docx, xlsx, pptx, html |
| `BlockType` | heading, paragraph, table, list, image, formula, toc |
| `InlineType` | run, hyperlink |

## 6. 未来预留（P3，不在当前实现范围）

`FootnoteNode` / `CommentNode` / `RevisionNode` / `ContentControlNode`（docx 高级特性，按需再开，不影响现有判别联合）。
