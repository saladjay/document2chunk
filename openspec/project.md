# document2chunk — 项目总览

> 面向 RAG 的多格式文档解析库。把可编辑 PDF、扫描件、DOCX（及未来的 XLSX/PPTX/HTML）转换为统一的「类型化文档树」逻辑结构，供下游语义切片、向量化、知识图谱使用。

## 1. 定位

- **不是**版面还原器（不算 bbox/页码/渲染）。
- **是**逻辑结构提取器：输出章节层级 + 段落/表格/列表/图片等内容节点。
- **是**多格式统一组件：一套规范 IR（类型化文档树）兼容 span（PDF/OCR）与 AST（docx）。

## 2. 核心设计原则

1. **规范 IR = 类型化文档树**。它同时兼容 span（PDF/OCR 的视觉重建产物）与 AST（docx 的语义直读产物）。
2. **结构与出处分离**：内容层级源无关；bbox/页码作为节点上**可选的 `provenance`** 元数据。
3. **各格式独立 extractor，统一输出 IR**；extractor 之间**禁止横向依赖**，只能依赖 `ir-model`。
4. **span 管线是「PDF/OCR 结构重建前端」**，不是规范 IR；其产出映射成 IR 节点。
5. **单体库 + 可选 HTTP**；不引入布局引擎（docx 不算 bbox/页眉页脚）。
6. **逻辑结构优先于排版结构**——目标是文章的章节/归属，而非像素级版面。

## 3. 能力清单（Capabilities）

| capability | 状态 | 说明 | 负责方 |
|---|---|---|---|
| `ir-model` | 🔒 契约冻结中 | 规范文档树（节点分类法 + provenance + 序列化） | Claude（最先冻结） |
| `pdf-extractor` | 规划中 | 可编辑 PDF：PyMuPDF + span 管线 → IR | **Qoder** |
| `ocr-extractor` | 规划中 | 扫描件/图片：PaddleOCR + 版面分析 → IR | Claude |
| `docx-extractor` | 规划中 | DOCX：lxml 直读 OpenXML → IR | Claude |
| `pipeline` | 规划中 | span 处理 Stage 引擎（pdf/ocr extractor 内部依赖） | 随 pdf-extractor |
| `structure-builder` | 规划中 | 章节树（栈算法）+ TOC 信号消费 | Claude |
| `export` | 规划中 | Markdown / JSON(AST) / PlainText / JSONL(兼容) | Claude |
| `api` | 规划中 | `parse()` 库入口 + FastAPI `/parse` | Claude |
| `debug` | 规划中 | 管线追踪（`debug_dir`）+ 可视化（bbox 叠加/结构树/阶段对比） | Claude（工具）+ Qoder（`debug_dir`） |
| `xlsx/pptx/html-extractor` | 🕒 未来 | 占位，树模型天然适配 | 待定 |

## 4. 开发分工与契约

- **共同契约 = `ir-model`**：Qoder 的 `pdf-extractor` 与 Claude 的其余模块都依赖它，故**最先冻结**。
- `pdf-extractor` 由 **Qoder** 基于现有 `doc-paddle-ocr`（~6400 行）迁移实现，需的输入：`ir-model` spec + `pdf-extractor` spec + 编码规范 + 复用边界评估。
- 其余模块由 **Claude** 编写。
- 源仓库：`D:\github\doc-paddle-ocr`（含 refraction1/refraction2/document-to-chunk 三套探索文档）。目标仓库：本仓 `document2chunk`。

## 5. 文档导航

| 文档 | 用途 |
|---|---|
| `designs/001-target-architecture.md` | **目标架构 + IR 定义（契约基准）** |
| `specs/<capability>/spec.md` | 各能力的行为契约（必须/禁止 + When/Then） |
| `docs/coding-standards.md` | Python 编码规范 |
| `proposals/` | 变更提案（迁移总提案等） |

## 6. SDD 流程

采用 OpenSpec/MSDP-workflow 文档结构（语言无关内核），Python 化裁剪：

`proposal → spec(行为契约) → design(ADR 式) → tasks → cc(校核)`
