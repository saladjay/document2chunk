# 并行开发方案与会话协作机制

> 目标：多开 Claude 会话并行实现各模块。本文定义**哪些可并行、怎么分组、会话之间如何通信**。
> 前置：`ir-model` 已实现+测试（`src/document2chunk/ir/`），是所有会话的共同契约。

## 1. 并行基础：依赖分层

```
Tier 0  ir-model ★冻结契约（已完成）—— 所有 session import，禁止修改节点定义
          │
Tier 1  仅依赖 ir-model（彼此无依赖 → 可全并行）
          ├── pdf-extractor + pipeline   ← 迁移自 doc-paddle-ocr
          ├── docx-extractor             ← lxml 全新
          ├── structure-builder          ← 章节树栈算法
          ├── export                     ← Markdown/JSON/PlainText/JSONL
          └── debug/viz                  ← 可视化（+ 可选 pipeline debug_dir 契约）
          │
Tier 2  依赖 Tier 1
          ├── ocr-extractor   ← 远程 PaddleOCR 服务 + markdown→IR（D11，不复用 pipeline）
          └── api             ← 集成层（须等所有 extractor + structure + export）
```

**关键**：Tier 1 各模块**两两无依赖**，是并行的主战场。`pipeline` 只服务 pdf-extractor（OCR 已按 D11 改远程服务，不再用 pipeline）。

## 2. 推荐会话切分（3 个并行 Claude）

按"家族"聚合，减少跨会话协调面（也可更细拆，见 §6）：

| Session | 负责 | 依赖 | 源码 |
|---|---|---|---|
| **① PDF 族** | `pdf-extractor` + `pipeline`（独占 `pipeline/`）；~~ocr-extractor~~ 已按 D11 移出（远程服务路线） | ir-model | 深度参考 `doc-paddle-ocr` |
| **② DOCX/结构/输出族** | `docx-extractor` + `structure-builder` + `export` | ir-model | 全新（lxml） |
| **③ 工具/集成族** | `debug/viz` + `api` | ir-model + 各模块握手契约 | 复刻 `visualize_pipeline.py` |

原把 pdf+ocr 同 session 是因共享 `pipeline`；OCR 已按 D11 改远程服务、不再用 pipeline，故 ocr-extractor **独立重做**（assignee 待定）。`api` 放 ③ 最后集成。

## 3. 协作机制（三层，会话间"通信"靠它）

会话之间无实时通道，**靠仓库里持久、可读的契约文件通信**：

### 3.1 冻结契约层 — `ir-model`（已就位）
- 所有 session `from document2chunk.ir import ...`。
- **禁止**任何 session 修改节点定义/字段。需要新节点类型 → 走协调人（本 Claude）统一加。

### 3.2 握手契约层 — `openspec/INTEGRATION.md`（会话间"API"）
定义 ir-model 未覆盖的**跨模块接口**（各 session 按此编码）：
- **Extractor 接口**：`extract(source, options) -> ExtractionResult(content, metadata, toc_entries)`
- **组装**：`structure.assemble(result, keep_toc=False) -> LogicalDocument`
- **pipeline `debug_dir` JSON schema**（① 写、③ debug 读）
- **export 入口**、**api 路由表**

> `ExtractionResult` 作为 ir-model 的**加性**类型（不改现有节点），由本 Claude 先加好。

### 3.3 消息板层 — `openspec/SESSIONS.md`
每个 session **开工前必读**，并 append-only 更新：
- **session 注册表**：谁负责什么 / 分支名 / 状态。
- **开放问题与决策日志**：阻塞项、已定决策。
- **接口变更日志**：改握手契约必须在此登记 + @ 相关 session。

## 4. 解耦关键：extractor 返回 `ExtractionResult`（非完整 LogicalDocument）

为让 extractor 与 structure-builder **完全独立**（都只依赖 ir-model），修正早前 spec 的"extractor → LogicalDocument"：

```
extractor.extract(...)  →  ExtractionResult(content[], metadata, toc_entries?)
                                    │
            api/orchestrator 调用   ▼
                  structure.assemble(result, keep_toc) → LogicalDocument
```

- extractor 只管"把源变成带 heading level 的 `content` + 元数据 + 可选 TOC"。
- structure-builder 只管"`content` → 章节树 + block_to_section"，并组装成 `LogicalDocument`。
- 两者**互不依赖**，可完全并行。`api` 负责接线。

## 5. 分支与合并

- 每个 session 一个分支：`feat/pdf-ocr` / `feat/docx-structure-export` / `feat/debug-api`。
- 基于含 `ir-model` 的 `main`。
- 冲突只会出现在**握手契约**（INTEGRATION.md）边界——由协调人裁定，不在 ir-model（冻结）。

## 6. 若要更细粒度并行（最多 5 个 Tier-1 session）

把 ①②③ 拆开：`pdf-extractor+pipeline`、`docx-extractor`、`structure-builder`、`export`、`debug/viz` 各一个 session；ocr 与 api 仍为 Wave 2。代价：协调面变大、`pipeline` 归属须显式指定给 pdf 那个 session。

## 7. 待补（reader 完成后）

- `openspec/designs/003-edited-pdf-source-summary.md`：doc-paddle-ocr 源码自汇总（pipeline/9-stage/parser/utils/detect/config 的精确契约与魔法数字）——给 session ① 的迁移依据。
- `openspec/sessions/<session>.md` ×3：每个 session 的自包含任务书（含源码要点、接口、验收）。
