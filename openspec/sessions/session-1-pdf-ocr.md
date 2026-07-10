# Session ① 任务书 — PDF/OCR 族（独占 pipeline）

> 你是一个并行 Claude 会话。本文件自包含，无需其他对话上下文。
> 分支：`feat/pdf-ocr`。状态：待开。

## 你的范围

- `pdf-extractor`（可编辑 PDF → IR）
- `pipeline`（span 处理引擎，9-Stage + SplitPipeline）—— **你独占**，全仓库只有你写 `src/document2chunk/pipeline/`
- `ocr-extractor`（扫描件/图片 → IR，复用 pipeline）
- `pipeline` 的 `debug_dir` 落盘机制（供 Session ③ debug 消费）

## 开工前必读（按顺序）

1. `openspec/project.md`
2. `openspec/designs/001-target-architecture.md`（IR 定义 §4）
3. `openspec/designs/002-pdf-extractor-reuse-map.md`（复用/丢弃清单）
4. `openspec/designs/003-edited-pdf-source-summary.md`（**源码自汇总，迁移权威依据**，含所有阈值与 bug）
5. `openspec/INTEGRATION.md`（你的接口契约 §2 §4）
6. `openspec/SESSIONS.md`（通信协议）
7. `openspec/specs/pdf-extractor/spec.md`、`openspec/specs/ocr-extractor/spec.md`
8. `src/document2chunk/ir/`（**只读 import，禁止改**）
9. `docs/coding-standards.md`

## 接口契约（必须遵守）

```python
def extract(source, *, options=None) -> ExtractionResult  # INTEGRATION §2
# 返回 content(HeadingNode.level 已判) + metadata(source_type) + toc_entries?
# PDF/OCR 节点带 provenance(page_index,bbox)；禁止内部调 structure-builder
```
`pipeline` 的 `debug_dir` JSON schema 见 INTEGRATION §4（`pages[].elements`，无顶层 elements）。

## 源码指针（`D:\github\doc-paddle-ocr\`）

- `pdf_parsers/pipeline/base.py`（Pipeline/SplitPipeline/Context）—— designs/003 §2
- `pdf_parsers/pipeline/stages/*.py`（9 Stage）—— designs/003 §4
- `pdf_parsers/pipeline/heading_scorer.py` —— designs/003 §5
- `pdf_parsers/parsers/parser_pymupdf.py`（提取/双引擎表格/排序/图片）—— designs/003 §6
- `pdf_parsers/common.py`、`pdf_parsers/pdf_detect.py`、`config.py` —— designs/003 §7

> designs/003 已汇总这些文件的精确契约、阈值、bug。先读它，按需再翻源码。

## 任务（对齐 `openspec/tasks.md` §2 §5 §9.1）

1. **迁移 pipeline**：9 Stage + `base.py`(G/L 引擎 + SplitPipeline 分流) + heading_scorer + common 保留项 → `src/document2chunk/pipeline/`。
   - **必须修**（designs/003 §2、§9）：`_stage_counter` 接力 → 共享 tracer；删 `saved_body` 补丁；修 `_redistribute` 键（`page_index` 非 `_page_index`）；SplitPipeline 延迟 import → 构造注入 stage 列表（解 DIP）。
   - 阈值/正则**原样保留**（designs/003 §4 表）。
2. **PyMuPDFSpanExtractor 补表格双引擎**：合并 `parser_pymupdf.py` 的 pdfplumber 优先 + PyMuPDF 兜底 + `_bbox_overlap`(提到模块级) + `sort_key`。
3. **新增 element(dict) → BlockNode 映射层**（designs/003 §8 映射表）：span→RunNode(provenance.bbox)、heading/title→HeadingNode、table→TableNode、image→ImageNode、toc_*/page_number 按规约处理。
4. **ocr-extractor**：PaddleOCR + 版面分析，source 感知降级（title 标签主信号、bold 失效降权、字号估算）；复用 pipeline 的 Classification/AutoLevel 但走 source 分支。
5. **debug_dir 落盘**：随 pipeline 迁移，schema 按 INTEGRATION §4。
6. **冒烟测试**：fixtures PDF → `LogicalDocument` 往返；heading 数量/层级与旧 JSONL 回归一致。

## 验收

- 可编辑 PDF → `LogicalDocument`（`source_type=pdf`，节点带 provenance）。
- `model_dump_json` 可往返；`visualize_debug_dir` 能消费你的 debug_dir（交给 Session ③ 验）。
- 扫描 PDF/图片 → `LogicalDocument`（`source_type=ocr`）。
- ir-model 冒烟测试仍绿（你没改它）。

## 协作注意

- **你独占 `pipeline/`**；Session ②③ 不会动它。ocr 也归你（复用 pipeline）。
- **禁止改 `ir-model`**。需要扩展 → 在 `SESSIONS.md §4` 提，协调人加。
- debug_dir schema 是与 Session ③ 的契约，**勿擅改**；要改先在 `SESSIONS.md §5` 登记。
- `parse()` 不归你（Session ③）；你只提供 `extract()`。
