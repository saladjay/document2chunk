# tasks — 实现任务分解

> 按 capability 切分，标注负责方。`ir-model` 是公共前置依赖。

## 1. 契约层（前置）

- [x] 1.1 定义规范 IR `LogicalDocument` + 全部节点类型（`designs/001` §4）
- [x] 1.2 实现 `src/document2chunk/ir/`（pydantic v2，判别联合 + 递归嵌套）
- [x] 1.3 契约冒烟测试 `tests/test_ir_smoke.py`（往返 + 深度查找）
- [x] 1.4 ir-model spec（`specs/ir-model/spec.md`）

## 2. pdf-extractor（Qoder）

- [ ] 2.1 迁移 9 Stage + `base.py`(SplitPipeline) + `heading_scorer` + `common.py` 到 `pipeline/`（按 `designs/002`）
- [ ] 2.2 `PyMuPDFSpanExtractor` 补表格双引擎（合并 `parser_pymupdf.py`）
- [ ] 2.3 新增 `element → BlockNode` 映射层（`specs/pdf-extractor` §4 映射表）
- [ ] 2.4 span → RunNode（含 provenance.bbox）
- [ ] 2.5 接 structure-builder、TOC 信号消费、page_number 丢弃
- [ ] 2.6 fixtures PDF 回归测试（heading 数量/层级与旧 JSONL 一致）

## 3. structure-builder（Claude）

- [ ] 3.1 栈算法构建 `section_tree` + `block_to_section`
- [ ] 3.2 `toc_map` 校准 + 可选 `TocNode`
- [ ] 3.3 边界用例（无标题/层级跳跃/level>9）

## 4. docx-extractor（Claude）

- [ ] 4.1 `PackageReader`（zipfile + lxml recover）
- [ ] 4.2 `StyleRegistry`（basedOn 继承链 + 缓存 + 循环检测）
- [ ] 4.3 `DocumentParser`（段落/表格/列表/图片/超链接）
- [ ] 4.4 标题检测（outlineLvl > pStyle链 > 启发式）
- [ ] 4.5 TOC 域识别 → 独立处理
- [ ] 4.6 fixtures docx 测试（Word/WPS/中文样式名）

## 5. ocr-extractor（Claude）—— 重做（D11：远程服务 + markdown→IR）

> 取代 session ① 旧 span 版。后端 = 远程 PaddleOCR 服务（见 `D:\project\server\PaddleOCR三件套使用文档.md`）。

- [ ] 5.1 `OcrServiceClient`（HTTP + token + 模型切换 + 健康检查，endpoint/token 配置注入）
- [ ] 5.2 模型选择（长文档→Unlimited / 复杂版式→VL / 公文→PP-OCRv6；可由 options 覆盖，默认 VL）
- [ ] 5.3 共享 `markdown→IR` 解析器（标题/表格/公式/图片/列表 → 节点；未来 html 复用）
- [ ] 5.4 provenance 从 `layoutParsingResults` 取（无则 None）；scanned/mixed PDF 经 pdf_detect 路由
- [ ] 5.5 `OcrServiceError` + 重试/超时；fixtures（mock 服务响应）测试

## 6. export（Claude）

- [ ] 6.1 `to_json`（规范，往返）
- [ ] 6.2 `to_markdown`（章节树遍历 + 表格/列表/图片）
- [ ] 6.3 `to_plain_text`、`to_jsonl`（兼容）

## 7. api（Claude）

- [ ] 7.1 `parse()` 源路由（扩展名 + pdf_detect）
- [ ] 7.2 调度 extractor → structure-builder
- [ ] 7.3 FastAPI `/parse` + `/health`

## 8. 集成与收尾

- [ ] 8.1 端到端：PDF/DOCX/OCR 各一份样本 → LogicalDocument → Markdown
- [ ] 8.2 编码规范落地检查（`docs/coding-standards.md`）
- [ ] 8.3 覆盖率达标（ir/structure ≥90%，extractor ≥80%）
- [ ] 8.4 README + 使用示例

## 9. debug / 可视化（Claude 工具 + Qoder 管线）

- [ ] 9.1 （Qoder）`pipeline` 迁移 `debug_dir` 落盘机制（`{NN}_{name}.json`，schema 见 `specs/debug` §2）
- [ ] 9.2 （Claude）`render_page` + `draw_annotations`（bbox 叠加，按 BlockType 配色 + 标签 + 统计面板）
- [ ] 9.3 （Claude）`draw_structure_tree`（章节树缩进视图，docx 主用）
- [ ] 9.4 （Claude）`generate_stage_comparison`（阶段对比条形图）
- [ ] 9.5 （Claude）`visualize(doc, ...)` / `visualize_debug_dir(...)` / `visualize_batch(...)` + CLI
- [ ] 9.6 源感知：PDF 叠加（有页面底图）；OCR 视服务 layout 结果（有 box 则叠加，否则结构树）；docx 结构树；PyMuPDF 缺失降级
