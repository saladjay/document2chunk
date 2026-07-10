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

## 5. 接口变更日志（append-only）

> 格式：`[日期] [session] 改了 INTEGRATION 哪条 — 影响 who — 状态`

- `（暂无）`
