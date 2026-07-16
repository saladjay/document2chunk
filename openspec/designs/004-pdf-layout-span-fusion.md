# 设计 004 — 版面分析 × span 双向融合（修首页误判）

> 状态：**提议（已实现 image_detection + table 校验）**
> 范围：可编辑 PDF 的 span 管线（`pipeline/stages/image_detection.py` + `extractors/pdf.py` 表格检测）
> 解决：首页/复杂版式页被误判成 **图片**（文字消失）或 **表格**（文字被吞）
> 参考：`D:\github\doc-paddle-ocr`（迁移源）；对方 IOU 方案的批判见 §5

---

## 1. 上下文与问题

可编辑 PDF 的首页（红头文件封面、公文模板、宣传页）常含**全页背景底纹、Logo、印章、装饰线**，且排版自由。现有 span 管线在两处误判：

1. **→ 图片**（`image_detection`）：用「文本中心点是否落入某 image bbox」判定。全页背景图覆盖整页 → 所有文字中心点都在图内 → 文字被替换为 `type=image` 占位符 → 首页正文在 Markdown 里变成 `[图片]` 或消失。
2. **→ 表格**（`extractors/pdf.py` 的 `_extract_raw_elements`）：pdfplumber/PyMuPDF `find_tables` 把封面排版（标题+发文单位+文号）误检为表格 → 这些文字被标 `type=table` 并在后续排除 → 内容丢失。

根因：**span 管线与版面分析是两个孤岛**——`layout_data`（PaddleOCR LayoutDetection）只被 `layout_filter` 用来删页眉页脚，**不参与** image/table 判定；而 image/table 检测对「这块区域语义上是什么」完全盲。

## 2. 关键洞察

- **span（PyMuPDF 文本）** = 内容 + 精确几何（可编辑 PDF 的 ground truth），但**不知语义角色**（图？表？背景？）。
- **版面分析** = 语义区域标签（text/title/figure/table/footer），但 bbox 粗、坐标系不同（136dpi vs 72pt）、可编辑 PDF 上**常缺**（`layout_jsonl` 可选，实践中很少传）。
- **PyMuPDF 原生非文本**（图片 bbox、`get_drawings()`、文字密度）= 零依赖的结构线索。`pdf_detect.py` 已算 `image_coverage = 图面积/页面积`（可复用）。

→ 三者**互补**：span 给内容，覆盖率给"是不是全页背景"，版面 label 给语义角色。融合方向是**双向**：
- **layout → span**：区域标签修正 image/table 的语义判定。
- **span → layout**：「这里有可编辑文字」反向证伪版面（被标 `figure` 但满是可编辑 span → 是带文字的背景，不是真图）。

## 3. 决策：三信号分层 + 双向验证 + 方向性包含

对**可编辑 PDF**，信号优先级（可靠度从高到低）：

1. **span 文字存在性**（主裁判，零依赖）：图 bbox 内有显著可编辑文字 → 是文字区/背景叠层 → **保留文字**。
2. **page_coverage**（次裁判，复用 `pdf_detect` 砖块）：图占页面比例 > 阈值且无文字 → 全页背景底纹 → **跳过**。
3. **layout label**（增强/裁决，可选）：有 `layout_data` 时，用「与图**交面积最大**的版面框的 label」裁决 span+覆盖率判不定的情形（如纯图无文字）。

**度量用方向性包含率，不用对称 IOU**（理由见 §5）：`contain = 交 / 参考框面积`。

### 3.1 image 分类决策表（`image_detection` 重写核心）

对每个 `image_info` 计算 `text_inside`（图 bbox 内可编辑文字元素数/面积）、`page_coverage`、（可选）`best_layout_label` + `contain`：

| 条件（按序短路） | 判定 | 动作 |
|---|---|---|
| `text_inside` 显著（元素数 ≥ `TEXT_IN_IMG_MIN` 或 文字面积/图面积 > `TEXT_RATIO_MAX`） | 背景/文字叠层 | **跳过**：不出占位符、**保留文字**（span 反向证伪） |
| `page_coverage > COVERAGE_BG` 且 `text_inside` 微弱 | 全页背景底纹 | **跳过** |
| 有 layout 且 best label ∈ {figure, image}（contain 高） | 真 figure | **出占位符**、替换其内文字 |
| 有 layout 且 best label ∈ {text, header, footer, table} | 非图区 | **跳过**（文字走 text 路径 / table 路径） |
| 其余（小图、无文字、无/含糊 layout） | 真 figure（Logo/示意图） | **出占位符**（≈现行行为） |

→ 「跳过」即**不替换文字**——这是修首页文字消失的关键。「出占位符」才走现行「图内文字→image 占位」逻辑。

### 3.2 table 校验（`extractors/pdf.py`，补「封面误表」）

`_extract_raw_elements` 检出候选表后、**文本排除前**做校验（降级的表其文字正常提取）：

保留条件（满足其一）：
- **layout-backed**：有 `layout_data` 且存在 `table` 框与候选表 bbox 高 `contain` 重叠；**或**
- **启发式**：行数 ≥ 2 且列数 ≥ 2 且非全空、网格较规整。

否则**降级**：不当表，其文本行作为普通 span 元素流出（封面/红头不再被吞）。

### 3.3 复用与坐标

- 覆盖率：复用 `pdf_detect.py` 的 `image_coverage` 口径（`LARGE_IMAGE_AREA_RATIO=0.5` 即 `COVERAGE_BG`）。
- 版面坐标：复用 `layout_filter._layout_to_pdf_coords`（136→72pt）+ box 结构 `result.res.boxes[].{label, coordinate}`。
- `classification` 已有 `style.layout_label` 钩子（OCR 路径在用）——PDF 路径若注入版面，可顺带给 span 打 label 提升 title 识别（本次不做，留 follow-up）。

## 4. 降级链（优雅退化）

| 条件 | 行为 |
|---|---|
| 有 `layout_data` | span+覆盖率+layout 三层全开，最准 |
| 无 `layout_data`（**可编辑 PDF 常态**） | span+覆盖率基线——**仍正确修首页背景**（不依赖 layout） |
| 极端：无图无文字 | 退化为现行小图逻辑 |

> 这正是相对「纯 IOU 方案」的核心优势：**头号收益（修首页背景）不挂在天花板条件（layout 存在）上**。

## 5. 为何不用纯 IOU（对方方案批判）

对方提议「image bbox 与版面 box 的 IOU 最大者的 label 为权威」。优点：可解释、比中心点鲁棒、复用 layout_data。但：

1. **硬依赖 layout_data，而可编辑 PDF 通常没有** → 头号收益在常见场景不触发，回退到有 bug 的中心点。
2. **IOU 是错的度量**（对称、惩罚双向尺寸差）：分不清「大图吞小文（背景）」与「小图在大文区里（真图）」——两者 IOU 都小。**该用方向性包含率**。
3. **「无重叠回退中心点」= 回退到要修的 bug**；且背景图常无整洁版面框或被整块误标 `figure`（IOU 全高 → 全判 figure → 更糟）。
4. **单方向、过度信任版面**：版面模型~90%，红头封面易整块误标；无 span 反向校验。
5. **没用到 span 这条最可靠信号**（可编辑文字存在性）。
6. **只修图、漏修表**。

本设计吸收其「layout 裁决」思想（§3.1 第 3 行），但改为：方向性包含、span 主裁判兜底、双向验证、并补 table 校验。

## 6. 常量（集中定义，去魔法数）

```python
# image_detection.py
COVERAGE_BG = 0.5            # 图占页面积 > 此 → 疑似全页背景（同 pdf_detect.LARGE_IMAGE_AREA_RATIO）
TEXT_IN_IMG_MIN = 3          # 图内可编辑文字元素 ≥ 此 → 文字叠层（保留文字）
TEXT_RATIO_MAX = 0.1         # 图内文字面积/图面积 > 此 → 文字叠层
LAYOUT_CONTAIN_MIN = 0.5     # 方向性 contain ≥ 此 → 视为该版面区域
# table 校验
TABLE_MIN_ROWS = 2
TABLE_MIN_COLS = 2
```

## 7. 验收场景

- 全页背景图 + 正文文字 → 文字**保留**（不变成 `[图片]`），不出背景图占位符。
- 正文中的示意图（小、无文字）→ 正常出 image 占位符。
- 有 layout：`figure` 框内的图 → 占位符；`text` 框内的图 → 跳过保留文字。
- 封面被误检为 1 行「表」→ **降级**为文字；真 2×2 表 → 保留为 TableNode。

## 8. 范围与 Follow-up

- **本次实现**：image_detection 三信号融合（§3.1）+ table 校验（§3.2）+ 测试。
- **未做（留 follow-up）**：把 layout 注入 PDF 前端给 span 打 `layout_label`，让 `classification` 的 title 钩子在可编辑 PDF 上也生效（红头大标题识别）。需评估「可编辑 PDF 是否值得为 layout 额外跑一次 PaddleOCR」。
- **可视化**：`debug` 模块（Session ③）的 `visualize_debug_dir` 可直观对比 image_detection 前后的占位符变化，便于调参。
