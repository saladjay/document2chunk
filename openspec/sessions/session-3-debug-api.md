# Session ③ 任务书 — 工具/集成族（debug + api）

> 你是一个并行 Claude 会话。本文件自包含。
> 分支：`feat/debug-api`。状态：待开。
> 注意：**debug 可先做（只依赖 ir-model）；api 是集成层，需各模块就绪后才能联调**。

## 你的范围

- `debug/viz`（管线追踪可视化 + IR 可视化）
- `api`（`parse()` 库入口 + FastAPI `/parse`，集成层）

## 开工前必读（按顺序）

1. `openspec/project.md`
2. `openspec/designs/001-target-architecture.md`（§9 接口、§4 IR）
3. `openspec/designs/003-edited-pdf-source-summary.md`（仅 §6 element schema + §2.4 debug_dir schema，供可视化消费）
4. `openspec/INTEGRATION.md`（你的接口契约 §4 §6 + 全局数据流 §1）
5. `openspec/SESSIONS.md`
6. `openspec/specs/debug/spec.md`、`specs/api/spec.md`
7. `src/document2chunk/ir/`（**只读 import**）
8. `docs/coding-standards.md`
9. （可视化复刻源）`D:\github\doc-paddle-ocr\visualize_pipeline.py`、`batch_visualize.py`

## 接口契约（必须遵守）

```python
# debug（INTEGRATION §1 数据流；消费 LogicalDocument 或 debug_dir）
def visualize(doc, source_path=None, out_dir="viz_out", *, dpi=150, pages=None, mode="overlay"|"tree"|"both") -> list[Path]
def visualize_debug_dir(debug_dir, source_path, *, dpi=150, pages=None, no_comparison=False) -> list[Path]

# api（INTEGRATION §6）
def parse(source, *, source_type=None, keep_toc=False, extract_images=True, options=None) -> LogicalDocument
# 路由: .pdf editable→pdf-extractor, scanned/mixed→ocr-extractor, .docx→docx-extractor, 图片→ocr
# 流程: extractor.extract() → structure.assemble() → LogicalDocument
```
debug_dir JSON schema 见 INTEGRATION §4（`pages[].elements`）。

## 任务（对齐 `openspec/tasks.md` §7 §9.2-9.6）

### debug/viz（先做，独立于其他模块）
1. `render_page`（PDF: PyMuPDF pixmap @dpi；OCR: 原图）+ PDF 坐标(72dpi)→像素换算。
2. `draw_annotations`（bbox 叠加，按 BlockType 配色 + 标签 type/level/字号/confidence + 顶部 header + 底部统计面板）。复刻 `visualize_pipeline.py`，但消费 `LogicalDocument` 的 `BlockNode.provenance.bbox`。
3. `draw_structure_tree`（章节树缩进视图，**docx 主用**，无 bbox）。
4. `generate_stage_comparison`（阶段对比条形图，消费 debug_dir）。
5. `visualize()` / `visualize_debug_dir()` / `visualize_batch()` + CLI（`python -m document2chunk.debug.visualize`）。
6. **源感知**：PDF/OCR→overlay；docx→tree；PyMuPDF 缺失→降级 tree + WARN。
7. 中文字体多平台 fallback。

### api（集成层，需各模块就绪）
1. `parse()` 源路由（扩展名 + `pdf_detect` 判 editable/scanned/mixed）。
2. 调度：`extractor.extract()` → `structure.assemble()` → `LogicalDocument`。
3. FastAPI `POST /parse`（multipart → `{document, markdown}`）+ `GET /health`。
4. 错误：不支持格式→`UnsupportedFormatError`；损坏→fast fail；可选依赖缺失→提示 extra。

## 验收

- debug：PDF/OCR doc → bbox 叠加 PNG；docx doc → 结构树；debug_dir → stage×page 图 + 对比图（与旧库等价）。
- api：`parse("a.pdf"/"a.docx"/扫描pdf)` 端到端产出 `LogicalDocument`；`/parse` HTTP 可用。
- ir-model 冒烟测试仍绿。

## 协作注意

- **debug 消费 Session ① 的 debug_dir**——schema 按 INTEGRATION §4，勿擅改；① 改了会在 `SESSIONS.md §5` 登记。
- **api 调用各 extractor 的 `extract()` + Session ② 的 `assemble()`/`export`**——这些接口就是你的依赖，按 INTEGRATION 编码。
- api 联调前可先用 mock extractor 跑通路由骨架。
- **禁止改 `ir-model` / `pipeline/` / 各 extractor**；你是消费者+集成者。
- 各模块未就绪时，优先推进 debug（独立可交付）。
