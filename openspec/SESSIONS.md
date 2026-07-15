# SESSIONS.md — 并行会话注册表与通信协议

> 多个 Claude 会话并行的"消息板"。每个 session **开工前必读本文件 + INTEGRATION.md + 自己的 spec + ir-model spec**。

## 1. 会话注册表

| Session | 分支 | 范围 | 依赖 | 状态 | 任务书 |
|---|---|---|---|---|---|
| **① PDF 族** | `feat/pdf-ocr` | `pdf-extractor` + `pipeline`（**独占 `pipeline/`**）；ocr-extractor 按 D11 移出本族、改远程服务 | ir-model | ✅ pdf-extractor+pipeline 已交付；ocr 按 D11 待重做 | `sessions/session-1-pdf-ocr.md` |
| **② DOCX/结构/输出族** | `feat/docx-structure-export` | `docx-extractor` + `structure-builder`（含 `assemble`）+ `export` | ir-model | ✅ 已交付（4 套测试绿）；`assemble`+`export` 可供 ③ 调用 | `sessions/session-2-docx-structure-export.md` |
| **③ 工具/集成族** | `feat/debug-api` | `debug/viz` + `api` | ir-model + INTEGRATION 握手 + 各模块就绪（api） | ✅ 已交付 | `sessions/session-3-debug-api.md` |

**为什么 pdf+ocr 同 session**：二者**共享 `pipeline`**，由 ① 独占编写，避免两方同时改 `pipeline/` 冲突。
**为什么 api 在 ③ 最后**：api 是集成层，需各 extractor + structure + export 就绪。
> D11 后：ocr 改走远程 PaddleOCR 服务 + markdown→IR，不再用本地 paddleocr span 管线；span 管线只留可编辑 PDF。

## 2. 开工前必读顺序（每个 session）

1. `openspec/project.md`（全局定位）
2. `openspec/designs/001-target-architecture.md`（IR 定义）
3. `openspec/INTEGRATION.md`（握手契约）
4. 本文件（通信协议 + 当前开放问题）
5. `openspec/specs/<自己的 capability>/spec.md`
6. `src/document2chunk/ir/`（import 契约，**只读不改**）
7. `docs/coding-standards.md`

## 3. 协调铁律

1. **`ir-model` 冻结**：禁止任何 session 改节点定义/字段。需要扩展 → 在 §4 开放问题提，由协调人统一加。
2. **`pipeline/` 归 ① 独占**：② ③ 不得写 `pipeline/`。
3. **握手契约改动必须登记**：改 `INTEGRATION.md` 接口 → 在 §5 接口变更日志记一行 + 注明影响谁。
4. **分支隔离**：各 session 在自己分支工作，基于含 ir-model 的 main；不交叉改他人模块。
5. **append-only**：§4 §5 只追加不删改历史；改主意新起一行。

## 4. 开放问题与决策日志（append-only）

> 格式：`[日期] [session] 问题/决策 — 结论`
> 初始为空，各 session 追加。

- `[初始] 协调人 — ir-model 冻结，新增 ExtractionResult/TocEntry 作为 extractor↔structure 握手 — 已实现+测过`
- `[2026-07-10] ① — Session ① 首版完成（feat/pdf-ocr @547525a）：pipeline + pdf-extractor + ocr-extractor（本地 paddleocr 版）迁移落地，冒烟测试全绿（ir 仍绿；pipeline 5/5、pdf 8/9、ocr stub 4/4）`
- `[2026-07-10] ① — 新增共享模块 document2chunk.errors（Document2ChunkError 基类 + InvalidSourceError/ExtractionError/PipelineError/OptionalDependencyError）— ②③ 可按 coding-standards §7 复用此基类；非 INTEGRATION 接口变更，未动 ir-model`
- `[2026-07-10] ① — element style 现携带 flags（取自 extractors.py 骨架，非旧 parser_pymupdf 生产路径）→ 激活 AutoLevel bold 规则（0.30）。这是相对旧 JSONL 的行为细化，按 designs/002「以 extractors.py 为骨架」指令；如需对齐旧基线可去掉 style.flags`
- `[2026-07-10] ① — extractors/__init__.py 为各 session 共享边界，本 session 仅放 docstring（与 ② 同文，add/add 合并应自动消解）；pdf/ocr 经 document2chunk.extractors.pdf / .ocr 导入`
- `[2026-07-10] ① — 真实 PaddleOCR 3.x 集成测试 inconclusive（predict 返回空，疑结果解析与 3.x 实际格式不符）；source 感知逻辑已由 stub 前端覆盖。待真机/真实扫描件验证 _iter_ocr_texts/_iter_layout_regions 解析`
- `[2026-07-10] session ③ — 新增共享异常模块 document2chunk.exceptions（Document2ChunkError 基类 + UnsupportedFormatError/MissingDependencyError/InvalidSourceError），并从顶层包导出 parse/异常。各 extractor/模块的异常基类请统一从此导入（coding-standards §7）。未改 ir-model / INTEGRATION。— session ①② 可知`
- `[2026-07-13] 集成 — ⚠️ 待协调人裁定：① 的 document2chunk.errors 与 ③ 的 document2chunk.exceptions 存在两个 Document2ChunkError 基类（重复）。集成 demo 两者并存各自导入可跑；正式需合并为单一异常模块。→ 已于 integration 修复（保留 exceptions，errors 内容并入，OptionalDependencyError 作别名，errors.py 删除）`
- `[2026-07-14] 协调人 — **D11**：OCR 后端改为远程 PaddleOCR 服务（PP-OCRv6/VL/Unlimited）→ markdown→IR，**弃本地 paddleocr**；span 管线只留可编辑 PDF。理由：强模型直接给结构化 markdown（表格/公式/图片），OCR 归入结构化源家族，去 bold/字号估算降级。服务见 D:\project\server\PaddleOCR三件套使用文档.md — 文档已落（ocr-extractor spec / designs/001 / tasks §5 / pyproject）`
- `[2026-07-15] 集成 → main 合并 — integration 的 pipeline + pdf-extractor 有效保留；① 早期 `extractors/ocr.py`（本地 paddleocr span 版）**按 D11 已过时**，待按「远程 PaddleOCR 服务 + markdown→IR」重做（新增 `parsers.markdown` + `OcrServiceClient`）。合并未删 ocr.py，留作参考/渐进替换`
- `[2026-07-15] ① — **`feat/ocr-remote` 废弃、不合并**：本会话并行重做的 D11 ocr（`parsers.markdown` + `extractors/_ocr_service.OcrServiceClient` + `extractors/ocr.py`，变量名 `PANDOCR_*`）与另一分支 `feat/ocr`（b5cab9f/2ee7954/077aac1，子包 `extractors/ocr/`：`_client/_markdown/_mapping/_chunker/_config/_exceptions/extractor.py` + F18 `InlineFormulaNode`）**重复**；后者更全（含公式/重试/`DOCUMENT2CHUNK_OCR_*` 配置）且已在 main。`feat/ocr-remote` 留 origin 作参考、不入 main；后续以 main 的 `extractors/ocr/` 为唯一 D11 实现`
- `[2026-07-15] ①（`feat/ocr-envconfig`）— 给 main 的 `extractors/ocr/_config.py` 加 `.env` 自动加载（`_load_dotenv`，从 cwd/上级 `.env` 读 `DOCUMENT2CHUNK_OCR_*`，真实环境变量优先；零依赖）+ `.gitignore` 忽略 `.env` + `.env.example` 模板。用法：`cp .env.example .env` 填 token，无需每次 export。未动他们在改的 `_chunker.py`/`_mapping.py``

## 5. 接口变更日志（append-only）

> 格式：`[日期] [session] 改了 INTEGRATION 哪条 — 影响 who — 状态`

- `[2026-07-10] session ③ — 未改 INTEGRATION；仅新增共享异常模块（见 §4）+ api 调度层（parse/extractor 注册/structure.assemble/export.to_markdown 均按 INTEGRATION §1-§6 惰性接入，缺失抛 MissingDependencyError）— 影响 session ①②：InvalidPdfError/InvalidDocxError 请继承 Document2ChunkError — 已在 integration 统一到 exceptions`
- `[2026-07-14] 协调人 — **OCR 架构改 D11**（远程服务 + markdown→IR，弃本地 paddleocr）。影响 **①**：其本地 span 版 `ocr-extractor` 作废需重做（pipeline 只留可编辑 PDF）。改动文件：`specs/ocr-extractor/spec.md`（重写）、`designs/001`、`project.md`、`tasks.md §5`、`pyproject.toml`（[ocr]→httpx、+viz）、`specs/api/spec.md`、`sessions/session-1`。新增组件：共享 `markdown→IR` 解析器（`parsers.markdown`）、`OcrServiceClient`。状态：文档已改，待 ① 重做 ocr-extractor`
