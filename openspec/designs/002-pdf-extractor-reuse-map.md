# 设计 002 — pdf-extractor 迁移复用地图

> 源仓库：`D:\github\doc-paddle-ocr`（~6400 行）
> 目标：将其可复用部分降格为 `pdf-extractor` / `pipeline` 内部实现，输出对接 `document2chunk.ir`。
> 依据：`refraction2/{ARCHITECTURE,SPEC,RULES,HANDOFF,TO-BE}.md` + 实际源码树。

## 1. 复用（→ `pdf-extractor` / `pipeline` 内部）

| 源文件 | 职责 | 复用动作 |
|---|---|---|
| `pdf_parsers/pipeline/stages/body_analysis.py` | 正文基准 (font,size) 统计 | 原样保留为 `BodyAnalysisStage` |
| `.../stages/classification.py` | 字号比值判定 heading/level | 保留；H1–4 阈值不变 |
| `.../stages/toc_detection.py` | 点线引导符识别目录页 | 保留 |
| `.../stages/toc_analysis.py` | 章节号/缩进推断标题层级 | 保留（作 TOC 信号消费） |
| `.../stages/layout_filter.py` | 过滤页眉/页脚/页码 | 保留（依赖 bbox，仅 PDF 用） |
| `.../stages/merge.py` | 同段多行合并 | 保留 |
| `.../stages/auto_level.py` | 多规则评分分配 level | 保留（confidence≥0.50） |
| `.../stages/image_detection.py` | 图片区域占位 | 保留 |
| `.../stages/page_number_detection.py` | 页码检测移除 | 保留 |
| `pdf_parsers/pipeline/base.py` | Pipeline / SplitPipeline / Context | 保留 SplitPipeline 分流；修掉 `_stage_counter` 手动传递 + saved_body（见 HANDOFF Phase 3） |
| `pdf_parsers/pipeline/heading_scorer.py` | `HeadingScoreAccumulator` 评分 | 保留；去掉冗余未用函数 |
| `pdf_parsers/common.py` | `normalize_font_size` / `infer_heading_level*` / `read_jsonl`/`write_jsonl` | 保留这几项；删死函数（setup_logging/ensure_dir/format_file_size/get_project_root） |
| `pdf_parsers/parsers/parser_pymupdf.py` | PyMuPDF 文本 + pdfplumber 表格双引擎 + 排序 | **拆分**：提取逻辑 → `PyMuPDFSpanExtractor` 扩展（加表格）；`_table_to_markdown`×3、`_bbox_overlap`、`sort_key` 提到模块级 |
| `pdf_parsers/pipeline/extractors.py: PyMuPDFSpanExtractor` | PyMuPDF→span（无表格） | 作为提取骨架复用，**补表格提取** |
| `pdf_parsers/pdf_detect.py` | editable/scanned/mixed 判定（≥70% 阈值） | 保留，用于源路由（scanned→ocr-extractor） |

> 表格双引擎（pdfplumber 优先、PyMuPDF 兜底，重叠>50% 排除文本）来自 ADR001，保留。

## 2. 替换（→ `document2chunk.ir`）

| 源 | 处理 |
|---|---|
| `pdf_parsers/pipeline/ir_base.py`（IRStyle/IRSpan/IRElement/IRPage） | **整体替换**为 `document2chunk.ir` 的类型化文档树。旧 span-IR 不再作为规范输出。 |
| `ir_element_to_pipeline_element()` 等 converter | 删除；改为 **element(dict) → `BlockNode`** 映射（见 pdf-extractor spec §映射表） |
| `pipeline-element dict`（运行时中间态） | 可作为 pipeline **内部**中间表示保留，但最终产出必须是 `LogicalDocument` |

## 3. 丢弃（不迁移）

| 源 | 原因 |
|---|---|
| `pdf_parsers/services/*`（docling/pymupdf4llm/unstructured/pdfplumber 微服务 + launcher + env_manager） | 微服务过度设计（ADR003/D9）；单体库 |
| `pdf_parsers/api/client.py`、`api/models.py: ServiceInfo` | 多服务 HTTP 客户端，不再需要 |
| `pdf_parsers/run_all.py`、`compare.py` | 批量/对比脚本，重构后按需重写 |
| `main.py`、`model_catalog.py` | 死代码 |
| `presets.py: full_pipeline/simple_pipeline` | 未用预设 |
| `parsers/parser_docling/pdfplumber/pymupdf4llm/unstructured.py` | 非主解析器（对比用），暂不迁 |
| `extractors.py: DocxSpanExtractor`、`OcrSpanExtractor` | docx 改 lxml（Claude 做）；OCR 归 ocr-extractor（Claude 做）。**不进 pdf-extractor** |
| 根目录散落脚本（analyze_layout/batch_*/debug_*/example_*/ocr_test_fix/match_and_ocr 等） | 实验/调试脚本，非核心 |

## 4. 工作量估计（pdf-extractor，供 Qoder 参考）

| 任务 | 量级 |
|---|---|
| 迁移 9 Stage + base + heading_scorer + common 到 `pipeline/` | 中（主要是搬运 + 删冗余，HANDOFF Phase1-3 已规划） |
| `PyMuPDFSpanExtractor` 补表格提取（合并 parser_pymupdf 的双引擎） | 中 |
| **新增 element→`BlockNode` 映射层**（含 RunNode+provenance） | 中（核心新代码） |
| 接 `structure-builder` 构建章节树 | 小（调接口） |
| 契约冒烟测试（fixtures PDF → LogicalDocument） | 中 |

> 净效果：源 ~6400 行 → pdf-extractor+pipeline 估计 ~2500–3000 行（剔除微服务/死代码/重复/非主解析器后，符合 TO-BE 的 ~34% 削减目标）。
