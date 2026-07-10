# pdf-extractor — 可编辑 PDF → IR 行为契约

> 实现方：**Qoder**
> 依赖：`document2chunk.ir`（契约，已实现）、`document2chunk.pipeline`（span 处理引擎）
> 复用地图：`designs/002-pdf-extractor-reuse-map.md`
> IR 定义：`designs/001-target-architecture.md` §4
> 本文档自包含，无需本对话上下文即可实现。

## 1. 职责

把**可编辑 PDF** 解析为 `LogicalDocument`（规范 IR）。内部沿用现有 span 管线（9-Stage + SplitPipeline），最后新增一层 `element → BlockNode` 映射产出 IR。

**输入**：PDF 文件路径（或 bytes）。
**输出**：`LogicalDocument`，`metadata.source_type = SourceType.PDF`。

## 2. 处理流程（复用现有 span 管线）

```
PDF → PyMuPDF 提取 spans（+ pdfplumber/PyMuPDF 表格双引擎）
    → SplitPipeline（G/L 分组 + 目录页/正文页分流）：
        1. BodyAnalysis(G)            → ctx.body_font, ctx.body_font_size
        2. ImageDetection(L) → Classification(L) → TOCDetection(L)
        3. 分流：
           · 目录页 → LayoutFilter(L) + PageNumberDetection(G)
           · 正文页 → LayoutFilter(L) → TOCAnalysis(G) → Merge(L) → AutoLevel(G) → PageNumberDetection(G)
    → element(dict) 列表（带 type/level/bbox/spans/page_index）
    → 【新增】element → BlockNode 映射
    → structure-builder 构建章节树
    → LogicalDocument
```

Stage 顺序、G/L 分组、`heading_confidence`、SplitPipeline 的 `saved_body` 保存恢复：**全部保留**现有行为（见 `doc-paddle-ocr/refraction2/SPEC.md` §5、RULES.md R1-R7）。

## 3. 需求

- **必须**：输出 `LogicalDocument`，`metadata.source_type = "pdf"`，`metadata.page_count` = PDF 页数。
- **必须**：每个产出的块节点携带 `provenance`（`source_type="pdf"`、`page_index`、`bbox`）。
- **必须**：PDF span 映射为 `RunNode`，span 的 `bbox` 落在 `RunNode.provenance.bbox`（**禁止**保留独立 span 类型）。
- **必须**：`content` 按 `(page_index, y_top, x0)` 排序（沿用现有 `sort_key`）。
- **必须**：标题层级 1–9；现有字号比值阈值（H1≥1.6×/H2≥1.3×/H3≥1.15×/H4≥1.05× × body_font_size）保留，AutoLevel confidence≥0.50 才赋级。
- **必须**：TOC 作信号消费——`toc_entry`/`toc_title` 用于校准标题层级，默认**不**进 `content`；仅当 `keep_toc=True` 时聚合为单个 `TocNode`。
- **必须**：`page_number` 类型元素**禁止**进入 `content`。
- **必须**：单段落/表格/页解析失败 → 记 WARN + 跳过 + 继续（graceful degradation）。
- **必须**：scanned/mixed PDF 不在本 extractor 范围——交由 `ocr-extractor`（通过 `pdf_detect` 路由）。

## 4. element(dict) → BlockNode 映射表（核心新代码）

现有管线产出 element dict（schema 见 SPEC.md §4）。按下表映射：

| element `type` | → IR 节点 | 说明 |
|---|---|---|
| `heading` | `HeadingNode` | `level` = element.level（1–9）；`text`；`runs` 见 §4.1 |
| `paragraph` | `ParagraphNode` | `text`；`runs` 见 §4.1 |
| `table` | `TableNode` | 由 pdfplumber 表格结构构造 rows/cells（见 §4.2） |
| `list` | `ListNode` | 若管线产出（PDF 列表识别有限）；否则忽略 |
| `image` | `ImageNode` | `image_id`、`format`、`width_emu/height_emu`、`alt`；`data` 默认不填 |
| `toc_entry` / `toc_title` | （信号） | 校准层级；`keep_toc` 时聚合 `TocNode` |
| `page_number` | （丢弃） | 不进 content |

每个块节点统一附加：
```python
provenance = Provenance(source_type=SourceType.PDF,
                        page_index=element["page_index"],
                        bbox=element["bbox"])
```

### 4.1 span → RunNode

```python
flags = span["flags"]
RunNode(
    id=next_run_id(),
    text=span["text"],
    style=RunProperties(
        font=span["font"],
        font_size=round(span["size"], 2),
        bold=bool(flags & 0x10) or _has_font_token(span["font"], "Bold"),
        italic=bool(flags & 0x02) or _has_font_token(span["font"], "Italic"),
    ),
    provenance=Provenance(source_type=SourceType.PDF,
                          page_index=element["page_index"],
                          bbox=span["bbox"]),
)
```

> `_has_font_token`：复用现有「字体名含 Bold/Italic/Oblique」判定。

### 4.2 表格 → TableNode

pdfplumber `extract_tables()` 返回 `list[list[list[str|None]]]`（行→单元格文本）。首行 `is_header=True`：

```python
TableNode(
    id=next_block_id(),
    provenance=Provenance(source_type=SourceType.PDF, page_index=page, bbox=table_bbox),
    rows=[
        TableRowNode(id=next_row_id(), is_header=(r == 0), cells=[
            TableCellNode(id=next_cell_id(), blocks=[
                ParagraphNode(id=next_block_id(), text=(cell or "").strip())
            ]) for cell in row
        ]) for r, row in enumerate(table_rows)
    ],
)
```

合并单元格（colspan/rowspan）：pdfplumber 不直接给，按现有 `_table_to_markdown` 的处理保持一致；本阶段可先平铺，合并信息记入 `metadata`。

## 5. 场景（When / Then）

- **当** 输入可编辑 PDF **那么** 返回 `LogicalDocument`，`source_type="pdf"`。
- **当** 管线判定某 element 为 heading level=2 **那么** 产出 `HeadingNode(level=2)`，`runs` 由其 spans 映射。
- **当** span `bbox=[x0,y0,x1,y1]`、`page_index=3` **那么** 对应 `RunNode.provenance.bbox=[...]`、`page_index=3`。
- **当** `keep_toc=False`（默认） **那么** `content` 中无 `toc_*`/`page_number` 节点。
- **当** `pdf_detect` 判定 scanned/mixed **那么** pdf-extractor 不处理（路由到 ocr-extractor）。
- **当** 某页表格提取失败 **那么** 记 WARN、该页表格降级为 `ParagraphNode(markdown)`、继续后续页。

## 6. 涉及实体（复用类）

`BodyAnalysisStage`、`ClassificationStage`、`TOCDetectionStage`、`TOCAnalysisStage`、`LayoutFilterStage`、`MergeStage`、`AutoLevelStage`、`ImageDetectionStage`、`PageNumberDetectionStage`、`SplitPipeline`、`PipelineContext`、`HeadingScoreAccumulator`（源自 `doc-paddle-ocr/pdf_parsers/pipeline/`，迁移至 `document2chunk/pipeline/`）。

## 7. 验收

- 对 ≥3 份可编辑 PDF 样本，产出 `LogicalDocument` 且 `model_dump_json` 可往返。
- heading 数量与现有 JSONL 输出一致；层级分布一致（回归基准：`doc-paddle-ocr/refraction2/HANDOFF.md`）。
- 契约冒烟测试：构造 PDF → `LogicalDocument` → 断言 `source_type`、provenance、RunNode 映射。
