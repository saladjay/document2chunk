# 设计 003 — edited-pdf 源码自汇总（迁移依据）

> 三套探索 md（refraction1/2）对源码的描述存在偏差，本文以**实际源码**为准汇总，作为 `pdf-extractor` / `pipeline` 迁移的权威依据。
> 源：`D:\github\doc-paddle-ocr\`。汇总对象：`pdf_parsers/pipeline/`、`pdf_parsers/parsers/parser_pymupdf.py`、`pdf_parsers/common.py`、`pdf_parsers/pdf_detect.py`、`config.py`。

## 1. 运行时管线顺序（`presets.py: default_pipeline`）

```
1. body_analysis          (G)  正文基准
2. image_detection        (L)  图片区域占位
3. classification         (L)  字号比值判 heading/level
4. layout_filter          (L)  过滤页眉/页脚/页码
5. toc_detection          (L)  识别目录页
6. toc_analysis           (G)  目录推断标题层级
7. merge                  (L)  段落合并
8. auto_level             (G)  多规则评分兜底赋级
9. page_number_detection  (G)  页码检测
```

G = `is_global=True`（合并所有页元素跑一次）；L = 逐页。`SplitPipeline` 在此基础上**分流**：正文页跑完整管线，目录页只跑 LayoutFilter + PageNumberDetection。

## 2. Pipeline 引擎（`pipeline/base.py`）

### 2.1 Stage Protocol
`@runtime_checkable`：`name:str`、`is_global:bool`、`process(elements, ctx) -> elements`。

### 2.2 G/L 分组（`_group_stages`）
连续相同 `is_global` 合段。`default_pipeline` 实际分组：`[G:body_analysis] → [L:image,classification,layout,toc_det] → [G:toc_analysis] → [L:merge] → [G:auto_level,page_number]`。

### 2.3 `page_offsets` 机制与假设
- Global 段：合并所有页元素 + 记录每页起止 offset → 跑 stage → 按 offset 切回各页。
- **核心假设**：global stage 透传不改元素数量（BodyAnalysis/AutoLevel/TOCAnalysis 满足）。
- 兜底 `_redistribute`：违反时按 `elem.get("_page_index")` 重分。⚠️ **bug**：机器注入的是 `elem["page_index"]`（无下划线），兜底读 `_page_index` → 几乎全归 page 0。

### 2.4 `debug_dir` 落盘（`_save_intermediate`）
`debug_dir=None` 零开销。非 None 时每 Stage 写 `{NN}_{name}.json`，**实际 schema**（md 说的"顶层 elements"是错的）：
```json
{"stage_index": N, "stage_name": "...", "stage_type": "global"|"local",
 "pages": [{"page_index": i, "elements": [...]}]}
```
`_stage_counter` 在此自增；`SplitPipeline` 跨子管线靠直接读写私有 `_stage_counter` 接力（反模式）。

### 2.5 SplitPipeline（5 Phase）
延迟 `from ...stages import ...` 9 个具体 Stage（违反 DIP）：
1. BodyAnalysis(G) → 2. ImageDetection+Classification+TOCDetection(L) → 3. 分流（`type in {toc_entry,toc_title}` 判目录页）→ 4. 目录页 LayoutFilter+PageNumberDetection → 5. 正文页 LayoutFilter→TOCAnalysis(全页跑,取正文)→Merge+AutoLevel+PageNumberDetection。
- **saved_body**：分流前快照 `(body_font,body_font_size,max_heading_level)`，合并后恢复（font/size 无条件恢复，level 取 max）。反模式，迁移时删。
- **`default_pipeline` ≠ `SplitPipeline`**：LayoutFilter 位置、TOCAnalysis 作用域都不同。生产入口是 **`split_pipeline`**。

## 3. PipelineContext 字段（每页一个；global 用 shared_ctx）

| 字段 | 写者 | 读者 | 备注 |
|---|---|---|---|
| `body_font` / `body_font_size` | body_analysis | classification, auto_level | 机器跨子管线同步 |
| `style_char_counts` | body_analysis | body_analysis | 内部累加 |
| `max_heading_level` | auto_level | （几乎无） | 机器用于同步 |
| `layout_data` | 外部注入 | layout_filter | 从 page_contexts[0] 同步到 shared_ctx |
| `image_infos` | parser 上游 | image_detection | 防御读取 |
| `page_width/height/index` | 上游注入 | layout_filter, auto_level | |
| `stats` | 多 stage | toc_detection | 子键：toc_entries/toc_pages/toc_mapping_count/... |
| `image_dir` / `pdf_stem` | — | — | **死字段** |

> ⚠️ **md 偏差纠正**：`toc_map`/`source_type`/`source_metadata` **不在** PipelineContext（是 IR 概念，迁移需补）。`toc_analysis` 的 `toc_mapping` 是局部变量，只把 count 记进 stats。

## 4. 9 个 Stage 契约（含阈值）

| Stage | G/L | 读 | 写/副作用 | 关键阈值 |
|---|---|---|---|---|
| **body_analysis** | G | spans.font/size | ctx.body_font/size | normalize 步长 0.2pt；空兜底 Unknown/12.0 |
| **image_detection** | L | ctx.image_infos | 文本元素→`type=image` 占位 | 交叠/元素面积 >0.5；无匹配占位 order=9999 |
| **classification** | L | ctx.body_*, style | type/level/heading_confidence/history | 字号容差 0.5pt；H1≥1.6×/H2≥1.3×/H3≥1.15×/H4≥1.05×；SKIP_TYPES={table,toc_*,list,image}；title=H1,heading=H2-4 |
| **layout_filter** | L | ctx.layout_data,page_* | **移除**元素 | LAYOUT_DPI=136/PDF_DPI=72；框扩展 page_h×5%；启发式页眉页脚 8%；NON_BODY_LABELS={number,header,footer,...}；中心点落入非正文框即丢 |
| **toc_detection** | L | text/bbox | type=toc_entry/toc_title | ≥3 连续点线判目录页；`_DOT_LEADER_RE`；标题关键词{目录,...}；同行合并 y_diff≤10(strict)/≤35(loose) |
| **toc_analysis** | G | toc_entry, 正文 paragraph | 正文→heading+level | depth_ratio≥0.5 走 depth 否则缩进；匹配分 exact0.70/prefix0.60/cleaned0.55；跳过 conf≥0.50；depth→level+1 |
| **merge** | L | type/level/style/bbox | 合并 text/bbox/spans | 段落同 level+字号差≤0.5pt+同字体；标题同行 y_diff≤5 |
| **auto_level** | G | ctx.body_*,page_*,flags | level/type=heading | conf≥0.50 赋级；仅独立行累加：section_num0.35/bold0.30/size_near0.25/font_diff0.20/line_gap0.15；bold 位 0x10；大间距 1.5×均值 |
| **page_number_detection** | G | bbox/text/page_index | type=page_number | 底部元素匹配正则；≥70% 页面命中才保留；5 条正则（`^\d+$`/`^第\d+页$`/...） |

**顺序耦合**（不可随意调换）：auto_level 的 `≥0.50 跳过` 依赖 classification(H1=0.50) 与 toc_analysis(0.55-0.70) 已先行赋分。toc 页必须跳过 layout_filter 的启发式页眉页脚（否则目录条目被毁）。

## 5. heading_scorer.py

- `HeadingScoreAccumulator(elem)`：`add_score(stage,rule,score,action)`，`_confidence=min(conf+score,1.0)`（action≠skip），`apply_to` 写回 `heading_confidence`(4位)+history。被 classification/toc_analysis/auto_level 共用。
- `is_standalone_line(elem,all,W,H)`：同行无他元素（中心 y 差≤3.0pt）+ 宽度<65%页宽 + 不在顶/底 8%。仅 auto_level 用。
- `extract_section_number`/`section_number_depth`/`is_pure_section_number`：章节号解析（auto_level + toc_analysis 用）。
- **评分统一建议**（HANDOFF Phase 4）：三套评分表（common 的字号比值表、auto_level 的几何表、toc 的匹配表）→ 统一为 `heading_scorer.RULE_WEIGHTS` 配置。

## 6. parser_pymupdf.py（主解析器）

- `parse()` 流程：图片提取 → 逐页 `_extract_raw_elements` → `pipeline.run` → `create_page_record` → 写 JSONL + images manifest。
- `_extract_raw_elements`：**pdfplumber 表格优先 + PyMuPDF 兜底**双引擎；文本行与表格重叠>50% 排除（`_bbox_overlap`）；`sort_key=(y_top,x0)` + 气泡 swap 重排 order_index；bold=字体名含"Bold"，italic 含"Italic/Oblique"。
- `_extract_all_images`：面积<`IMAGE_MIN_AREA`(1000) 跳过；命名 `p{page}_{idx}.png`；三级降级取 bytes（xref→位置匹配→pixmap 兜底 DPI=150）。
- `to_markdown()`：type→MD 映射；page_number 跳过；heading `#`×min(level,6)。
- **JSONL schema**：element = `type/label/level/text/markdown/bbox/order_index/style{font,size,bold,italic}/spans[]/heading_confidence/heading_level_conf_history`；page = `page_index/page_number/elements/metadata{width,height,body_font_size,body_font,...}/stats`。
- **反模式**：`_bbox_overlap`/`sort_key`/`clean_cell` 内嵌闭包；`_table_to_markdown`×3 重复；输出路径硬编码 `cwd/pdf_parsers/outputs`；pdfplumber 逐页重开 PDF。

## 7. common.py / pdf_detect.py / config.py

**common.py**：保留 `normalize_font_size`(0.2pt 网格)、`infer_heading_level_with_score`+`_HEADING_SCORE_MAP`、`read_jsonl`、`format_duration`；删 4 死函数（setup_logging/ensure_dir/format_file_size/get_project_root）+ 孤立 `write_jsonl`。

**pdf_detect.py**：`detect_pdf_type` → editable/scanned/mixed（`DOCUMENT_RATIO=0.7`）；页面级：大图(coverage≥0.5)→scanned，否则文本≥30字→editable，否则 coverage≥0.3→scanned，否则 empty。删 fitz 回退。

**config.py**：在用 `IMAGE_MIN_AREA=1000`/`IMAGE_FORMAT=png`/`PAGE_NUMBER_PATTERNS`/`PAGE_NUMBER_THRESHOLD_RATIO=0.7`/`PDF_DPI=200`(OCR 栅格)。死代码 `get_layout_kwargs`/`get_ocr_kwargs` + 9 个 PaddleOCR 常量（OCR 未集成）。
> ⚠️ **DPI 纠正**：`LAYOUT_DPI=136`/`PDF_DPI=72` **不在 config.py**，而在 `layout_filter.py:27-28`（版面坐标 136→PDF 72pt 换算）。config 的 `PDF_DPI=200` 是 OCR 栅格化，**两套量纲不可合并**。

## 8. element(dict) → BlockNode 映射（对齐 `document2chunk.ir`）

| element `type` | → IR 节点 | 备注 |
|---|---|---|
| `title`(H1)/`heading`(H2-9) | `HeadingNode(level,text,runs)` | level 取 element.level；`heading_confidence`/history → `HeadingNode.metadata` |
| `paragraph` | `ParagraphNode(runs,text)` | |
| `table` | `TableNode(rows)` | 由 `element.markdown` 或 pdfplumber grid 重建 rows/cells |
| `list` | `ListNode` | PDF 列表识别有限 |
| `image` | `ImageNode(image_id,format,width_emu,height_emu,alt)` | |
| `toc_entry`/`toc_title` | （TOC 信号） | 校准层级；`keep_toc` 时聚 `TocNode` |
| `page_number` | （丢弃） | 不进 content |

- span → `RunNode`：`text/style(font,size,bold,italic)`+`provenance=Provenance(source_type=pdf,page_index,bbox=span.bbox)`。
- element → 块级 `provenance=Provenance(pdf, page_index, element.bbox)`。
- `content` 按 `(page_index,y_top,x0)` 排序；metadata.body_font/size 从 ctx 提升到 `DocumentMetadata.custom` 或文档级基准。

## 9. 迁移地图（细化 designs/002）

- **搬**：9 Stage + heading_scorer + common(保留项) + G/L 引擎 + SplitPipeline 分流结构 + pdf_detect + 表格双引擎。
- **重构**：`_stage_counter`→共享 tracer；删 `saved_body`；修 `_redistribute` 键；SplitPipeline 延迟 import→构造注入；新增 element→BlockNode 映射层；`PipelineContext` 收敛（删死字段、补 source_type 走 Provenance）。
- **丢弃**：`ir_base.py` 旧 span-IR、`full/simple_pipeline`、microservices、死代码、`to_markdown`(IR 上重写)、PaddleOCR kwargs。
