# 提案 001 — 多格式文档解析器（迁移重构）

## 为什么

现有 `doc-paddle-ocr`（~6400 行）只解析可编辑 PDF，且以扁平 span/bbox 为中心。三套重构探索文档（`refraction1`/`refraction2`/`document-to-chunk`）在「统一 IR 用 span 还是文档树」「docx 库选型」「bbox 哲学」上互相冲突。

经仲裁（见 `澄清1.md` + `designs/001`）：**以类型化文档树为规范 IR**，兼容 span（PDF/OCR）与 AST（docx）。现需新建 `document2chunk` 作为迁移目标，统一架构、消除冲突，并为 xlsx/pptx/html 预留扩展。

## 变更内容

- 新建仓库 `document2chunk`，采用 OpenSpec/MSDP-workflow 的 SDD 流程。
- 定义规范 IR `LogicalDocument`（类型化文档树，结构与出处分离）。
- 将现有 PDF span 管线**降格**为 `pdf-extractor` 的结构重建前端，复用 9-Stage + SplitPipeline。
- 新增 `docx-extractor`（lxml 直读）与 `ocr-extractor`（PaddleOCR + 版面分析）。
- 新增 `structure-builder`（章节树）、`export`（Markdown/JSON/PlainText/JSONL）、`api`（库入口 + FastAPI）。
- 预留 xlsx/pptx/html extractor 接口。

## 功能（Capabilities）

| capability | spec | 负责 | 状态 |
|---|---|---|---|
| `ir-model` | `specs/ir-model/spec.md` | Claude | ✅ 契约已实现+测试 |
| `pdf-extractor` | `specs/pdf-extractor/spec.md` | **Qoder** | ⏳ 待实现 |
| `docx-extractor` | `specs/docx-extractor/spec.md` | Claude | ⏳ |
| `ocr-extractor` | `specs/ocr-extractor/spec.md` | Claude | ⏳ |
| `structure-builder` | `specs/structure-builder/spec.md` | Claude | ⏳ |
| `export` | `specs/export/spec.md` | Claude | ⏳ |
| `api` | `specs/api/spec.md` | Claude | ⏳ |
| `xlsx/pptx/html-extractor` | — | 待定 | 🕒 未来 |

## 影响

- **代码**：新仓库；从 `doc-paddle-ocr` 迁移 `pdf_parsers/pipeline/*` 与 `parser_pymupdf.py`（复用地图见 `designs/002`）；丢弃微服务/死代码/非主解析器。
- **依赖**：`pydantic>=2`（核心）；optional extras：`[pdf]` PyMuPDF+pdfplumber、`[docx]` lxml、`[ocr]` paddleocr+Pillow、`[api]` fastapi+uvicorn。
- **契约**：`ir-model` 为全局唯一契约，最先冻结，所有方 import。
- **团队**：Qoder 与 Claude 并行（按 capability 切分，依赖 `ir-model`）。
