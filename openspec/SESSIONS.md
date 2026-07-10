# SESSIONS.md — 并行会话注册表与通信协议

> 多个 Claude 会话并行的"消息板"。每个 session **开工前必读本文件 + INTEGRATION.md + 自己的 spec + ir-model spec**。

## 1. 会话注册表

| Session | 分支 | 范围 | 依赖 | 状态 | 任务书 |
|---|---|---|---|---|---|
| **① PDF/OCR 族** | `feat/pdf-ocr` | `pdf-extractor` + `pipeline` + `ocr-extractor`（**独占 `pipeline/`**） | ir-model | 待开 | `sessions/session-1-pdf-ocr.md` |
| **② DOCX/结构/输出族** | `feat/docx-structure-export` | `docx-extractor` + `structure-builder`（含 `assemble`）+ `export` | ir-model | 待开 | `sessions/session-2-docx-structure-export.md` |
| **③ 工具/集成族** | `feat/debug-api` | `debug/viz` + `api` | ir-model + INTEGRATION 握手 + 各模块就绪（api） | 待开 | `sessions/session-3-debug-api.md` |

**为什么 pdf+ocr 同 session**：二者**共享 `pipeline`**，由 ① 独占编写，避免两方同时改 `pipeline/` 冲突。
**为什么 api 在 ③ 最后**：api 是集成层，需各 extractor + structure + export 就绪。

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
2. **`pipeline/` 归 ① 独占**：② ③ 不得写 `pipeline/`；ocr 也归 ①。
3. **握手契约改动必须登记**：改 `INTEGRATION.md` 接口 → 在 §5 接口变更日志记一行 + 注明影响谁。
4. **分支隔离**：各 session 在自己分支工作，基于含 ir-model 的 main；不交叉改他人模块。
5. **append-only**：§4 §5 只追加不删改历史；改主意新起一行。

## 4. 开放问题与决策日志（append-only）

> 格式：`[日期] [session] 问题/决策 — 结论`
> 初始为空，各 session 追加。

- `[初始] 协调人 — ir-model 冻结，新增 ExtractionResult/TocEntry 作为 extractor↔structure 握手 — 已实现+测过`
- `[2026-07-10] ① — Session ① 首版完成（feat/pdf-ocr @547525a）：pipeline + pdf-extractor + ocr-extractor 迁移落地，冒烟测试全绿（ir 仍绿；pipeline 5/5、pdf 8/9、ocr stub 4/4）`
- `[2026-07-10] ① — 新增共享模块 document2chunk.errors（Document2ChunkError 基类 + InvalidSourceError/ExtractionError/PipelineError/OptionalDependencyError）— ②③ 可按 coding-standards §7 复用此基类；非 INTEGRATION 接口变更，未动 ir-model`
- `[2026-07-10] ① — element style 现携带 flags（取自 extractors.py 骨架，非旧 parser_pymupdf 生产路径）→ 激活 AutoLevel bold 规则（0.30）。这是相对旧 JSONL 的行为细化，按 designs/002「以 extractors.py 为骨架」指令；如需对齐旧基线可去掉 style.flags`
- `[2026-07-10] ① — extractors/__init__.py 为各 session 共享边界，本 session 仅放 docstring（与 ② 同文，add/add 合并应自动消解）；pdf/ocr 经 document2chunk.extractors.pdf / .ocr 导入`
- `[2026-07-10] ① — 真实 PaddleOCR 3.x 集成测试 inconclusive（predict 返回空，疑结果解析与 3.x 实际格式不符）；source 感知逻辑已由 stub 前端覆盖。待真机/真实扫描件验证 _iter_ocr_texts/_iter_layout_regions 解析`

## 5. 接口变更日志（append-only）

> 格式：`[日期] [session] 改了 INTEGRATION 哪条 — 影响 who — 状态`

- `（暂无）`
