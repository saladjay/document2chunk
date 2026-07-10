# debug — 管线追踪与可视化行为契约

> 实现方：Claude（可视化工具）+ Qoder（管线 `debug_dir` 随 `pipeline` 迁移）
> 依赖：`document2chunk.ir`、（可选）`document2chunk.pipeline`
> 复刻来源：`doc-paddle-ocr/visualize_pipeline.py`、`batch_visualize.py`、`pipeline/base.py:_save_intermediate`
> 目标：新库具备与旧库**同等的可视化过程**，并泛化为可消费规范 IR + 源感知。

## 1. 职责（两类能力）

1. **管线追踪（trace）**：span 管线每个 Stage 执行后落盘中间状态（`debug_dir`），便于观察元素逐级演化。
2. **可视化（visualize）**：把「最终 `LogicalDocument`」或「debug_dir 中各 stage 中间态」渲染成 PNG，复刻旧库的 bbox 叠加 + 统计面板 + 阶段对比图。

## 2. 管线追踪（`debug_dir`）

> 归属 `pipeline` 模块，随 pdf-extractor 迁移（见 `designs/002`）。

- **必须**：`Pipeline` / `SplitPipeline` 接受 `debug_dir: str | Path | None`；非 None 时每个 Stage 执行后写盘。
- **必须**：文件名 `{NN}_{stage_name}.json`（`NN` = stage 序号，2 位）；`SplitPipeline` 跨子管线保持序号连续（沿用现有 `_stage_counter`，待按 HANDOFF Phase 3 重构为构造参数）。
- **必须**：每文件 schema：
  ```json
  {"stage_index": N, "stage_name": "...", "stage_type": "global"|"local",
   "pages": [{"page_index": i, "elements": [<pipeline element dict>, ...]}]}
  ```
- **必须**：`debug_dir=None` 时**零开销**（不写盘、不构造 record）。

## 3. 可视化

### 3.1 输入（两种模式）

| 模式 | 输入 | 用途 |
|---|---|---|
| **IR 可视化** | `LogicalDocument`（+ 源文件路径） | 看最终解析结果 |
| **过程可视化** | `debug_dir`（stage JSON）+ 源文件路径 | 看各 Stage 逐级演化（复刻旧库） |

- **必须**：两种模式共用同一套绘制函数（`draw_annotations` / `render_page`）。
- **必须**：元素来源统一从 `BlockNode.provenance`（IR 模式）或 element dict（过程模式）取 bbox/type/level/style。

### 3.2 源感知（关键泛化）

| 源 | 是否有页面底图 | 视图 |
|---|---|---|
| PDF / OCR | 有（PyMuPDF 渲染 / 原图） | **bbox 叠加视图**（§3.3） |
| DOCX | 无（不算 bbox） | **结构树视图**（§3.4） |
| 混合 | — | 两种视图都生成 |

- **必须**：`doc.provenance`（或 element bbox）为 None 时**禁止**尝试 bbox 叠加，改走结构树视图。

### 3.3 bbox 叠加视图（PDF/OCR）

复刻 `visualize_pipeline.py: draw_annotations`：

- **必须**：渲染源页面为底图（PDF：`fitz` pixmap @ `dpi`；OCR：`PIL.Image.open` 原图）。
- **必须**：PDF 坐标（72 DPI）→ 像素：`coord × (dpi/72)`。
- **必须**：按 `BlockType` 配色画 `provenance.bbox` 矩形（配色见 §5，复刻并扩展 `TYPE_COLORS`）。
- **必须**：标签含 `type`、heading `level`、`style.font_size`（pt）、OCR `confidence`。
- **必须**：顶部 header（source_type + page_index + [stage 名，过程模式]）。
- **必须**：底部统计面板（元素总数、各类型计数、正文基准 font/size、颜色图例）。

### 3.4 结构树视图（全源，docx 主用）

- **必须**：渲染 `section_tree` 为缩进树：每个 `SectionNode` 一行（缩进 = level，显示 title + level + heading_node_id），其下挂 `block_ids` 的块摘要（type + 文本前 N 字）。
- **必须**：不渲染页面、不依赖 bbox；docx 仅有此视图。
- **可选**：导出为文本树或 SVG/PNG（PIL 绘制）。

### 3.5 阶段对比图（过程模式）

- **必须**：复刻 `generate_stage_comparison`——每页一张条形图，展示各 stage 的类型分布变化；仅消费 `debug_dir`。

## 4. API

```python
# IR 可视化（最终结果）
def visualize(
    doc: LogicalDocument,
    source_path: str | Path | None = None,   # PDF/图片底图；docx 可省略
    out_dir: str | Path = "viz_out",
    *,
    dpi: int = 150,
    pages: list[int] | None = None,
    mode: Literal["overlay", "tree", "both"] = "both",
) -> list[Path]: ...

# 过程可视化（debug_dir）
def visualize_debug_dir(
    debug_dir: str | Path,
    source_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    dpi: int = 150,
    pages: list[int] | None = None,
    no_comparison: bool = False,
) -> list[Path]: ...

# 批量
def visualize_batch(sources: list[Path], **kwargs) -> None: ...
```

- **必须**：`mode` 自动按源选择：PDF/OCR 默认 `overlay`（+ 可选 tree）；docx 默认 `tree`。
- **必须**：提供 CLI：`python -m document2chunk.debug.visualize <doc.json|debug_dir> [source] [opts]`。
- **必须**：source_path 缺失时（PDF/OCR 叠加需要）→ 提示并提供仅结构树降级。

## 5. 配色与字体

- **必须**：`TYPE_COLORS` 复刻旧库并扩展到新 `BlockType`：
  `heading` 橙、`paragraph` 蓝、`table` 紫、`list` 青、`image` 灰、`formula` 品红、`toc` 深绿、`page_number`(过程态) 黄。
- **可选**：heading 按 level 渐变色（L1 深橙→L9 浅橙）。
- **必须**：中文字体多平台 fallback（复刻 `FONT_PATHS`：Windows msyh/simsun、Linux NotoCJK、macOS PingFang），找不到降级 PIL 默认字体 + WARN。

## 6. 依赖

- **必须**（viz）：`Pillow`（叠图/树图）。
- **可选**：`PyMuPDF`（PDF 页面渲染，`[pdf]` extra）；缺失时 PDF 叠加降级为仅结构树。
- docx 结构树视图：无重依赖（PIL 或纯文本）。

## 7. 场景（When / Then）

- **当** `visualize(pdf_doc, source_path="a.pdf")` **那么** 生成每页 bbox 叠加 PNG（block 按类型配色 + 统计面板）。
- **当** `visualize(docx_doc)`（无 source） **那么** 生成结构树视图，无页面渲染。
- **当** `visualize_debug_dir(debug_dir, "a.pdf")` **那么** 每个 stage×page 一张叠加图 + 阶段对比图（与旧库输出等价）。
- **当** OCR 文档某 block `confidence=0.3` **那么** 叠加标签含 `conf=0.30`。
- **当** `debug_dir=None` 跑管线 **那么** 无任何盘写入（零开销）。
- **当** PDF 模式但未装 PyMuPDF **那么** 降级为结构树视图 + WARN。

## 8. 涉及实体

`LogicalDocument`、`BlockNode`、`Provenance`、`SectionNode`、`DocumentMetadata`；`TYPE_COLORS`、`FONT_PATHS`、`render_page_background`、`draw_annotations`、`draw_structure_tree`、`generate_stage_comparison`。
