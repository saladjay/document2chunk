# API_SPEC — document2chunk 接口规范

> 状态：**规范文档（汇总）** · v1 · 2026-07-14
> 范围：库入口 `parse()`、HTTP `/parse` `/health`、extractor 接口、`structure.assemble`、`export`、IR 契约摘要、debug 可视化、异常与错误模型、可选依赖。
> 权威来源（冲突时以代码 + 下列契约为准）：
> - 握手契约：`openspec/INTEGRATION.md`
> - 接口设计：`openspec/designs/001-target-architecture.md` §9
> - 行为契约：`openspec/specs/<capability>/spec.md`
> - 已实现签名：`src/document2chunk/`（`api.py`、`exceptions.py`、`ir/`、`debug/`）
>
> 本文件是上述散落接口契约的**单一汇总视图**，便于下游集成与对外联调。新增/变更接口请同步更新本文件，并按 `INTEGRATION.md §7` / `SESSIONS.md §5` 登记。

---

## 1. 概述

document2chunk 把**可编辑 PDF / 扫描件·图片 / DOCX**（未来 xlsx/pptx/html）解析为统一的**类型化文档树** `LogicalDocument`，供 RAG 下游切片/检索/图谱使用。

- **单体库** + 可选 HTTP（designs/001 D9）：`from document2chunk import parse` 即用；需要 HTTP 再装 `[api]` extra。
- **结构与出处分离**：内容层级源无关；bbox/页码是节点上**可选**的 `provenance`（PDF/OCR 携带，docx 不携带）。
- **统一 IR**：所有 extractor 输出同一 `LogicalDocument`；extractor 之间禁止横向依赖（D10）。

### 1.1 读者

- **库集成方**：读 §3（`parse()`）、§8（IR）、§10（错误）。
- **HTTP 调用方**：读 §4（HTTP）、§10（错误码）。
- **extractor/模块实现方**（并行 session）：读 §5、§6、§7、§9，并以 `INTEGRATION.md` 为握手基准。

---

## 2. 架构与数据流

```
source ──▶ api.parse() ──▶ extractor.extract() ──▶ ExtractionResult(content, metadata, toc_entries?)
            (源路由)            (pdf/docx/ocr)            │
                                                          ▼ api 调用
                                            structure.assemble(result, keep_toc)
                                                          │
                                                          ▼
                                                   LogicalDocument
                                            ┌──────────┬──────────┐
                                            ▼          ▼          ▼
                                         export    debug/viz    下游 RAG
```

**依赖方向（铁律，designs/001 §6）：**

```
api → extractors → ir-model
                 ↘ pipeline ↗        (pdf extractor 内部；ocr 已改远程服务，不走 span 管线)
structure-builder → ir-model
export → ir-model
debug → ir-model (+ 可选 PyMuPDF/Pillow)
```

- `ir-model`（`src/document2chunk/ir/`）是**零业务依赖**的叶子契约（仅 pydantic），**最先冻结、最稳定**，任何模块只导入不改。
- `api` 是唯一接线点：调用 extractor → `structure.assemble`。
- extractor 之间**禁止横向依赖**（D10）。

---

## 3. 库入口 `parse()`

> 实现：`src/document2chunk/api.py` · 契约：`INTEGRATION.md §6`、`specs/api/spec.md`

### 3.1 签名

```python
from document2chunk import parse, LogicalDocument
from document2chunk.api import ParseOptions

def parse(
    source: str | Path | bytes | bytearray,
    *,
    source_type: SourceType | str | None = None,   # None = 自动判定（扩展名 + 魔数 + pdf_detect）
    keep_toc: bool = False,                          # True 时导出 TocNode（designs/001 D7）
    extract_images: bool = True,                     # False 时 ImageNode.data=None
    options: ParseOptions | None = None,             # 透传给 extractor
) -> LogicalDocument: ...
```

### 3.2 `ParseOptions`

```python
class ParseOptions(BaseModel):
    model_config = ConfigDict(extra="allow")   # 容许 extractor 自定义字段（如 ocr_model）
    dpi: int = 150
    extract_tables: bool = True
    extract_images: bool = True                # 被 parse() 的 extract_images 覆盖
```

### 3.3 源路由（`source_type=None` 时自动判定）

| 输入特征 | 路由到 | `metadata.source_type` |
|---|---|---|
| `.pdf` + `pdf_detect` 判 **editable**（≥70% 页阈值） | `pdf-extractor` | `pdf` |
| `.pdf` + **scanned / mixed** | `ocr-extractor` | `ocr` |
| `.docx` | `docx-extractor` | `docx` |
| 图片（png/jpg/jpeg/bmp/tif/tiff/gif/webp） | `ocr-extractor` | `ocr` |
| 其它 | — | 抛 `UnsupportedFormatError` |

- **显式 `source_type` 优先于自动判定**。显式 `source_type="pdf"` 直接走 editable 路线（不再跑 pdf_detect）。
- **bytes 输入**：无扩展名时按**魔数嗅探**（`%PDF-`→pdf、PNG/JPG/BMP/TIFF/GIF→image、`PK`→docx）。
- **PDF editable/scanned 判定**：默认走 `document2chunk.pipeline.pdf_detect.detect_pdf_type`（session ①）；不可用时退化为 PyMuPDF 文本覆盖率启发式（≥30 字/页 → editable）。可用 `api.set_pdf_kind_detector(fn)` 注入自定义判定器。

### 3.4 调度语义

1. 路由得到 `SourceType` → 解析对应 extractor（惰性导入；未就绪 → `MissingDependencyError`）。
2. `result = extractor.extract(source, options=opts)`（`opts.extract_images` 已设为 `parse()` 的入参）。
3. `doc = structure.assemble(result, keep_toc=keep_toc)`。
4. 回填元数据：`metadata.source_file`（路径名）、`metadata.source_type`（若 extractor 未设）。

---

## 4. HTTP API（FastAPI，`[api]` extra）

> 实现：`api.py:create_app()` · 契约：`specs/api/spec.md §4` · 启动：`python -m document2chunk.api [--host 127.0.0.1] [--port 8000]`

### 4.1 `GET /health`

```http
GET /health
```
```json
{ "status": "ok", "version": "0.1.0" }
```

### 4.2 `POST /parse`

**请求**（二选一）：

- **multipart**（推荐，需 `python-multipart`）：字段 `file`（或 `document`）= 二进制。
- **原始请求体**（`Content-Type: application/octet-stream`，无需 multipart 依赖）：body = 文件二进制，可用 `?filename=` 提供文件名以辅助判定。

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `source_type` | string | 自动 | `pdf`/`ocr`/`docx`...；省略则按魔数/扩展名判定 |
| `keep_toc` | bool | `false` | 是否导出 `TocNode` |
| `extract_images` | bool | `true` | 是否提取图片二进制 |

**响应 `200`**：

```json
{
  "document": { /* LogicalDocument JSON（exclude_none）*/ },
  "markdown": "# 第一章\n\n正文内容。"
}
```

- `document` = `LogicalDocument.model_dump_json(exclude_none=True)` 解析后的对象（见 §8）。
- `markdown` = `export.to_markdown(doc)`；`export` 未就绪时为 `null`（可由 `api.set_markdown_renderer(fn)` 注入）。

**示例（curl，原始请求体）**：

```bash
curl -X POST "http://127.0.0.1:8000/parse?source_type=docx&filename=a.docx" \
     -H "content-type: application/octet-stream" \
     --data-binary @a.docx
```

**示例（multipart）**：

```bash
curl -X POST "http://127.0.0.1:8000/parse?keep_toc=true" \
     -F "file=@a.pdf"
```

### 4.3 错误码

| HTTP | 异常 | 触发 |
|---|---|---|
| `400` | `UnsupportedFormatError` | 不支持的格式 / 路由失败；multipart 缺 `file` 字段 |
| `422` | `Document2ChunkError`（含 `InvalidDocxError` / `InvalidPdfError` / `FileTooLargeError`） | 源损坏/缺失关键部分/超限（fast fail） |
| `503` | `MissingDependencyError` / `OcrServiceError` | 可选依赖缺失或模块未就绪；OCR 远程服务不可达 |

> 当前实现（`api.py`）注册了 `UnsupportedFormatError→400`、`MissingDependencyError→503`、`Document2ChunkError→422` 三个处理器。`OcrServiceError→503`、`FileTooLargeError→413` 待对应 extractor 落地后补专用处理器（见 §10）。

---

## 5. Extractor 接口

> 契约：`INTEGRATION.md §2` · 实现：`api.py:Extractor` Protocol

### 5.1 统一 Protocol

```python
@runtime_checkable
class Extractor(Protocol):
    source_type: SourceType
    def extract(
        self,
        source: str | Path | bytes | bytearray,
        *,
        options: ParseOptions | None = None,
    ) -> ExtractionResult: ...
```

- **必须**返回 `ExtractionResult`；`content` 中 `HeadingNode.level` 已判定（1–9）；`metadata.source_type` 已设。
- **必须**：PDF/OCR 节点带 `provenance`（`page_index`/`bbox`）；docx 节点 `provenance=None`。
- **禁止**：extractor 内部调用 structure-builder 或直接产出完整 `LogicalDocument`。
- **测试/联调**：`api.register_extractor(source_type, extractor)` 注入实例（含 mock）。

### 5.2 `ExtractionResult`（extractor → structure-builder 握手）

> 实现：`src/document2chunk/ir/result.py`

```python
class ExtractionResult(BaseModel):
    content: List[BlockNode]                       # 已判定 level 的扁平块序列（阅读顺序）
    metadata: DocumentMetadata                     # source_type 已设
    toc_entries: Optional[List[TocEntry]] = None   # 可选目录条目（校准 level / 导出 TocNode）

class TocEntry(BaseModel):
    text: str
    level: Optional[int]       # 1–9
    page: Optional[int]        # 0-based
```

### 5.3 各 extractor 特性

| extractor | 处理源 | 关键依赖 | 主要异常 | 备注 |
|---|---|---|---|---|
| `pdf-extractor`（`extractors.pdf.PdfExtractor`） | 可编辑 PDF | `[pdf]` PyMuPDF+pdfplumber；内含 `pipeline`（9 Stage span 管线） | `InvalidPdfError` | span→`RunNode`，bbox 落 `provenance`；实现方 Qoder |
| `docx-extractor`（`extractors.docx.DocxExtractor`） | `.docx` | `[docx]` lxml | `InvalidDocxError`、`FileTooLargeError` | lxml 直读 OpenXML；**所有节点 `provenance=None`**（D6） |
| `ocr-extractor`（`extractors.ocr.OcrExtractor`） | 扫描件/图片/mixed PDF | **远程 PaddleOCR 服务**（D11，HTTP 客户端；**禁用本地 paddleocr**） | `OcrServiceError` | 服务返回 markdown → 共享 `markdown→IR` 解析器；`options.ocr_model` 可指定（VL/PP-OCRv6/Unlimited-OCR），endpoint/token/超时由配置注入 |

> **OCR 远程服务**（specs/ocr-extractor §3-4）：按输入选模型（长文档→Unlimited-OCR、复杂版式→PaddleOCR-VL、规整公文→PP-OCRv6，默认 VL）；所有请求带 `Authorization: Bearer <token>`，**禁止硬编码 token**；服务不可达/超时/模型未就绪 → `OcrServiceError`。

---

## 6. structure-builder：`assemble()`

> 实现：`src/document2chunk/structure/` · 契约：`INTEGRATION.md §3`、`specs/structure-builder/spec.md`

```python
def assemble(
    result: ExtractionResult,
    *,
    keep_toc: bool = False,
) -> LogicalDocument: ...
```

- 单遍**栈算法**构建 `section_tree`（根 `SectionNode(id="sec_root", title="ROOT", level=0)`）+ `block_to_section`，时间 O(n)、空间 O(d)。
- 层级规则：`HeadingNode(level=N)` 挂到栈中 `level<N` 的最近章节下；正文块归当前栈顶。
- 可选 `toc_entries` 校准 `HeadingNode.level`；`keep_toc=True` 时产出单个 `TocNode`（默认不进 `content`）。
- **唯一**生产 `section_tree` / `block_to_section` 的模块（禁止旁路修改）。

---

## 7. export

> 实现：`src/document2chunk/export/` · 契约：`INTEGRATION.md §5`、`specs/export/spec.md`

| 函数 | 签名 | 说明 |
|---|---|---|
| `to_json` | `(doc, *, pretty=True) -> str` | **规范输出**：`model_dump_json(exclude_none=True)`，可无损往返 |
| `to_markdown` | `(doc, *, include_metadata=False) -> str` | 遍历 `section_tree`；标题 `#`×level(≤6)；表格管道表格；列表 `- `/`1. `；`include_metadata=True` 加 YAML front matter |
| `to_plain_text` | `(doc) -> str` | 按 `content` 阅读顺序拼接，表格行 `\t` 连接 |
| `to_jsonl` | `(doc) -> str` | 兼容旧 PDF 接口（**非规范**） |

`POST /parse` 的 `markdown` 字段即 `to_markdown(doc)`。

---

## 8. IR 契约摘要（`LogicalDocument`）

> 完整定义：`designs/001 §4`、`specs/ir-model/spec.md` · 实现：`src/document2chunk/ir/models.py`（pydantic v2）

### 8.1 顶层结构

```python
class LogicalDocument(BaseModel):
    metadata: DocumentMetadata
    content: List[BlockNode]                       # 扁平阅读序列
    section_tree: SectionNode                       # 章节层级（根 level=0，自包含嵌套）
    block_to_section: Dict[str, str]                # block_id → section_id
```

`DocumentMetadata`：`title, author, source_type, source_file, created, modified, page_count, generator, custom(dict)`。

### 8.2 节点类型（判别联合，`type` 为 discriminator）

| 块节点 | 关键字段 |
|---|---|
| `HeadingNode` | `level(1–9)`, `text`, `runs[]` |
| `ParagraphNode` | `runs[]`(RunNode/HyperlinkNode), `text` |
| `TableNode`→`TableRowNode`→`TableCellNode` | `cell.blocks[]`, `colspan`, `rowspan`, `is_header` |
| `ListNode`→`ListItemNode` | `ordered`, `items[]`, `level` |
| `ImageNode` | `image_id`, `format`, `width_emu`, `height_emu`, `alt`, `data?` |
| `FormulaNode` | `latex?`, `text?` |
| `TocNode` | `entries[]`（仅 `keep_toc=True` 出现） |

行内：`RunNode`(`text`, `style:RunProperties`)、`HyperlinkNode`(`target`, `runs[]`)。**span 统一映射为 `RunNode`**（PDF span / docx `<w:r>`），其 bbox 落在 `RunNode.provenance.bbox`。

### 8.3 provenance 约定

```python
class Provenance(BaseModel):
    source_type: SourceType
    page_index: Optional[int]      # 0-based（PDF/OCR）
    bbox: Optional[List[float]]    # [x0,y0,x1,y1]
    confidence: Optional[float]    # 0.0–1.0（OCR）
```

- PDF/OCR 节点携带；**docx 节点 `provenance=None`**（D6，禁止塞 bbox/页码）。

### 8.4 序列化与 ID

- 规范输出：`model_dump_json(exclude_none=True)`；`model_validate_json` 无损往返。
- ID 单文档内唯一稳定：`block_/sec_/run_` + 6 位（`block_000001`）。
- `content` 保持源阅读顺序：PDF/OCR 按 `(page_index, y_top, x0)`；docx 按 `<w:body>` 顺序。

### 8.5 枚举

`SourceType`：`pdf, ocr, docx, xlsx, pptx, html`；`BlockType`：`heading, paragraph, table, list, image, formula, toc`；`InlineType`：`run, hyperlink`。

---

## 9. debug 可视化

> 实现：`src/document2chunk/debug/` · 契约：`specs/debug/spec.md`

```python
def visualize(
    doc: LogicalDocument,
    source_path: str | Path | None = None,    # PDF/图片底图（叠加视图所需）
    out_dir: str | Path = "viz_out",
    *,
    dpi: int = 150,
    pages: list[int] | None = None,
    mode: Literal["overlay", "tree", "both"] = "both",
) -> list[Path]: ...

def visualize_debug_dir(
    debug_dir: str | Path,
    source_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    dpi: int = 150,
    pages: list[int] | None = None,
    no_comparison: bool = False,
) -> list[Path]: ...

def visualize_batch(sources: list[str | Path], **kwargs) -> None: ...
```

- **源感知**：PDF/OCR（有底图）→ bbox 叠加视图；docx/无 source → 结构树视图；PyMuPDF 缺失 → 降级结构树 + WARN。
- **过程模式**：消费 `debug_dir`（`{NN}_{stage}.json`，schema 见 `INTEGRATION.md §4`），每 stage×page 一图 + 阶段对比图。
- **CLI**：`python -m document2chunk.debug.visualize <doc.json|debug_dir> [source] [--mode] [--dpi] [--pages] [--out-dir] [--no-comparison]`。

---

## 10. 异常与错误模型

> 共享基类：`src/document2chunk/exceptions.py`（各 extractor/模块的异常继承 `Document2ChunkError`，coding-standards §7）

```
Document2ChunkError                      # 基类（顶层包导出）
├── UnsupportedFormatError               # api：格式不支持/路由失败          → HTTP 400
├── MissingDependencyError               # api：可选依赖缺失/模块未就绪      → HTTP 503
├── InvalidSourceError                   # 共享基类：源损坏/缺关键部分
│   ├── InvalidDocxError                 # docx-extractor（document.xml 缺失/损坏）→ HTTP 422
│   └── InvalidPdfError                  # pdf-extractor                       → HTTP 422
├── FileTooLargeError                    # docx-extractor：超上限             → HTTP 413（待补）
└── OcrServiceError                      # ocr-extractor：远程服务不可达/超时  → HTTP 503
```

| 异常 | 抛出方 | HTTP | 现状 |
|---|---|---|---|
| `UnsupportedFormatError` | api | 400 | ✅ 已实现 |
| `MissingDependencyError` | api | 503 | ✅ 已实现 |
| `Document2ChunkError`（兜底） | — | 422 | ✅ 已实现（覆盖未特化的子类） |
| `InvalidDocxError` / `InvalidPdfError` | docx/pdf extractor | 422 | ⏳ 待 session ①②（继承 `InvalidSourceError`） |
| `FileTooLargeError` | docx extractor | 413 | ⏳ 待补处理器 |
| `OcrServiceError` | ocr extractor | 503 | ⏳ 待 session ①（需补专用处理器） |

---

## 11. 可选依赖（extras）

> 定义：`pyproject.toml [project.optional-dependencies]`

| extra | 安装命令 | 依赖 | 用途 |
|---|---|---|---|
| `pdf` | `pip install document2chunk[pdf]` | PyMuPDF, pdfplumber | 可编辑 PDF 解析 + PDF 页面渲染 |
| `ocr` | `pip install document2chunk[ocr]` | （HTTP 客户端，**远程服务**） | 扫描件/图片 OCR（D11） |
| `docx` | `pip install document2chunk[docx]` | lxml | DOCX 解析 |
| `api` | `pip install document2chunk[api]` | fastapi, uvicorn, **python-multipart** | HTTP 服务 |
| `dev` | `pip install document2chunk[dev]` | pytest, pytest-cov | 测试 |

> **已知不一致（待协调人修 `pyproject.toml`）**：
> 1. `[ocr]` 仍列 `paddleocr`，但 D11 已弃用本地 paddleocr、改远程 HTTP 服务——应改为 HTTP 客户端（如 `httpx`）。
> 2. `[api]` 缺 `python-multipart`（multipart `/parse` 上传所需）——当前 `/parse` 已支持原始请求体回退，但 multipart 需补此依赖。

---

## 12. 变更规则

- 改 `INTEGRATION.md` 任何接口 → 必须在 `SESSIONS.md §5` 登记，并 @受影响 session。
- `ir-model` **冻结**：需新节点/字段 → 走协调人（统一加性扩展 + 更新 `specs/ir-model/spec.md` + 冒烟测试），各 session 不得私改。
- 本文件（`API_SPEC.md`）随接口变更同步更新。

---

## 附录 A：`POST /parse` 响应示例（docx）

```json
{
  "document": {
    "metadata": { "source_type": "docx", "source_file": "a.docx" },
    "content": [
      { "id": "block_000001", "type": "heading", "level": 1, "text": "第一章" },
      { "id": "block_000002", "type": "paragraph", "text": "正文内容示例。" }
    ],
    "section_tree": {
      "id": "sec_root", "title": "ROOT", "level": 0,
      "subsections": [
        { "id": "sec_000001", "title": "第一章", "level": 1,
          "heading_node_id": "block_000001", "block_ids": ["block_000002"], "parent_id": "sec_root" }
      ]
    },
    "block_to_section": { "block_000001": "sec_000001", "block_000002": "sec_000001" }
  },
  "markdown": "# 第一章\n\n正文内容示例。"
}
```

## 附录 B：`POST /parse` 响应示例（PDF，节点带 provenance）

```json
{
  "document": {
    "metadata": { "source_type": "pdf", "source_file": "a.pdf", "page_count": 1 },
    "content": [
      { "id": "block_000001", "type": "heading", "level": 1, "text": "Hello Title",
        "provenance": { "source_type": "pdf", "page_index": 0, "bbox": [70.0, 100.0, 220.0, 125.0] } }
    ],
    "section_tree": { "id": "sec_root", "title": "ROOT", "level": 0 },
    "block_to_section": {}
  },
  "markdown": "# Hello Title"
}
```
