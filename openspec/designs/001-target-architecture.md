# 设计 001 — 目标架构（TO-BE）与规范 IR 定义

> 状态：**契约基准（冻结中）**
> 作者：架构对齐（Claude + 产品方）
> 上游决策来源：`doc-paddle-ocr/refraction1`、`refraction2`、`document-to-chunk` 三套文档的冲突仲裁结论 + `澄清1.md`

---

## 1. 上下文

现有 `doc-paddle-ocr`（~6400 行）以 PyMuPDF **span** 为中心，已实现可编辑 PDF 解析：9-Stage 管线（BodyAnalysis→Classification→TOCDetection→ImageDetection→LayoutFilter→TOCAnalysis→Merge→AutoLevel→PageNumberDetection）+ SplitPipeline 分流 + 三级标题判定。

两轮重构探索（`refraction1/2`）尝试把 DOCX/OCR 塞进同一 span 管线（docx 用 python-docx 模拟 bbox），暴露两个根本问题：

1. **docx 模拟 bbox 不可靠**——AutoLevel 的「独立行」判定依赖 bbox，而 docx 是流式文档，模拟值无法支撑（survey v2 §6.4 自列风险）。
2. **span/bbox 模型难扩展**——xlsx（网格）、pptx（幻灯片）、html（DOM）套不进「带 bbox 的 span」。

`document-to-chunk` 给出了 docx 的另一条路：**类型化文档树（AST）**，lxml 直读、`outlineLvl` 确定性标题、章节树。

**仲裁结论（已与产品方对齐，见 `澄清1.md`）**：以「类型化文档树」为**规范 IR**，它同时容纳 span（可编辑 PDF 的视觉重建产物）、AST（docx 的语义直读产物）与结构化 markdown（OCR 服务输出，见 D11）。本仓 `document2chunk` 为迁移目标。

---

## 2. 目标 / 非目标

**目标**

- G1 定义一套**源无关**的规范文档树 IR，作为所有格式的统一输出。
- G2 PDF（可编辑）/ DOCX / OCR 三源先落地；预留 xlsx/pptx/html 的 extractor 接口。
- G3 复用现有 PDF span 管线投资，将其**降格为「可编辑 PDF 结构重建前端」**，产出喂给 IR（OCR 不再走 span 管线，见 D11）。
- G4 单体库（`parse()` 入口）+ 可选 HTTP（FastAPI `/parse`）。

**非目标**

- N1 不做版面还原/渲染。
- N2 **docx 不算 bbox / 页码 / 页眉页脚**；未来若下游强需页码定位，新增**独立布局模块**（docx→PDF）+ 一个 IR 元数据字段，不污染主架构。
- N3 edited-pdf 路线**不做**批注/修订/内容控件；docx 路线列为 P3，视需求再开。
- N4 不做手写体 OCR、不专门处理多栏（沿用现有 Out-of-Scope）。

---

## 3. 关键决策（ADR 摘要）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 规范 IR = 类型化文档树（非扁平 span 列表） | 业界标杆 Docling 以类型化 `DocItem` 层次支撑 PDF/DOCX/PPTX/HTML；树模型天然适配多格式 |
| D2 | span 管线降为**可编辑 PDF** 结构重建前端 | 保住算法投资（BodyAnalysis/AutoLevel/TOCAnalysis），仅服务可编辑 PDF；OCR 改走远程服务（D11） |
| D3 | docx 用 lxml 直读 OpenXML（弃 python-docx） | 减依赖、可读 outlineLvl 与样式继承链、确定性标题 |
| D4 | 标题层级 H1–9（修正旧管线 H1–4 的设计错误） | docx 由 outlineLvl(0–8) 确定；PDF 启发式可靠到 H1–4 但不强制全局压平 |
| D5 | 结构与出处分离：`provenance` 为可选节点元数据 | 统一 page 型源（PDF/OCR 有 bbox/page）与 flow 型源（docx 无） |
| D6 | docx 版面信息（bbox/页眉/页码）整体退出范围 | 流式文档逻辑结构已足够 RAG；见 N2 |
| D7 | TOC 作「信号」消费（校准标题层级）+ 可选 `TocNode` 导出 | docx 目录域与 PDF 目录页都识别后走同一独立流程，不混入正文 |
| D8 | 规范输出 = LogicalDocument JSON(AST)；JSONL 仅作兼容导出 | 树形便于下游切片/检索；JSONL 兼容旧 PDF 接口 |
| D9 | 单体库 + 可选 HTTP，不做微服务 | 旧微服务只因选型期依赖冲突；包能共存即不拆服务 |
| D10 | extractor 间禁止横向依赖，只能依赖 `ir-model` | 保证各格式可独立开发/并行（Qoder 做 pdf，Claude 做其余） |
| D11 | OCR 后端 = 远程 PaddleOCR 服务（PP-OCRv6 / PaddleOCR-VL / Unlimited-OCR），markdown→IR；**弃本地 paddleocr** | 强模型直接给结构化 markdown（表格/公式/图片），OCR 归入「结构化源」家族（同 docx/html），去掉 bold/字号估算降级；span 管线只留可编辑 PDF。服务见 `D:\project\server\PaddleOCR三件套使用文档.md` |

---

## 4. 规范 IR 定义（核心契约）

### 4.1 设计要点

- **双视图**：`content`（扁平阅读序列，导出/序列化用）+ `section_tree`（章节层级，切片/检索用）并存，由同一批节点支撑。
- **span = RunNode**：PDF 的 span、docx 的 `<w:r>` 统一成 `RunNode`；PDF span 的 bbox 落在 `RunNode.provenance.bbox`。
- **类型化节点**：pydantic v2 判别联合（discriminated union），以 `type` 字段判别。
- **provenance 可选**：PDF 节点携带；OCR 视服务 `layoutParsingResults`（可选）；docx 节点全空。
- **稳定 ID**：`block_000001` / `sec_000001` / `run_000001`，跨格式一致。

### 4.2 数据模型（pydantic v2 伪码）

```python
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal, Union
from enum import Enum

# ============ 枚举 ============

class SourceType(str, Enum):
    PDF = "pdf"          # 可编辑 PDF
    OCR = "ocr"          # 扫描件/图片
    DOCX = "docx"
    XLSX = "xlsx"        # 未来
    PPTX = "pptx"        # 未来
    HTML = "html"        # 未来

class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    FORMULA = "formula"
    TOC = "toc"                 # 可选导出（D7）
    # 未来/P3: FOOTNOTE, COMMENT, REVISION

class InlineType(str, Enum):
    RUN = "run"
    HYPERLINK = "hyperlink"

# ============ 出处（可选） ============

class Provenance(BaseModel):
    """节点出处。PDF/OCR 携带；docx 默认不携带。"""
    source_type: SourceType
    page_index: Optional[int] = None       # PDF/OCR 页码（0-based）
    bbox: Optional[List[float]] = None     # [x0, y0, x1, y1]
    confidence: Optional[float] = None     # OCR 置信度

# ============ 样式 ============

class RunProperties(BaseModel):
    """字符/段落级样式。docx 经样式继承链解析；PDF 来自 span flags；OCR 多为未知。"""
    font: Optional[str] = None
    font_size: Optional[float] = None      # pt
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    strikethrough: Optional[bool] = None
    color: Optional[str] = None            # hex，如 "#FF0000"
    highlight: Optional[str] = None
    is_superscript: Optional[bool] = None
    is_subscript: Optional[bool] = None

# ============ 行内节点 ============

class InlineNode(BaseModel):
    id: str
    type: InlineType
    provenance: Optional[Provenance] = None

class RunNode(InlineNode):
    type: Literal[InlineType.RUN] = InlineType.RUN
    text: str = ""
    style: Optional[RunProperties] = None

class HyperlinkNode(InlineNode):
    type: Literal[InlineType.HYPERLINK] = InlineType.HYPERLINK
    target: str
    runs: List[RunNode] = Field(default_factory=list)

# ============ 块节点（判别联合） ============

class BlockNode(BaseModel):
    id: str
    type: BlockType
    provenance: Optional[Provenance] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)  # 源特有信息透传

class HeadingNode(BlockNode):
    type: Literal[BlockType.HEADING] = BlockType.HEADING
    level: int = Field(..., ge=1, le=9)     # H1–9
    text: str
    runs: List[RunNode] = Field(default_factory=list)

class ParagraphNode(BlockNode):
    type: Literal[BlockType.PARAGRAPH] = BlockType.PARAGRAPH
    runs: List[InlineNode] = Field(default_factory=list)   # RunNode / HyperlinkNode
    text: str = ""                                          # 便捷纯文本

class TableCellNode(BaseModel):
    id: str
    blocks: List[BlockNode] = Field(default_factory=list)  # 可嵌套段落/列表/子表格
    colspan: int = 1
    rowspan: int = 1

class TableRowNode(BaseModel):
    id: str
    cells: List[TableCellNode] = Field(default_factory=list)
    is_header: bool = False

class TableNode(BlockNode):
    type: Literal[BlockType.TABLE] = BlockType.TABLE
    rows: List[TableRowNode] = Field(default_factory=list)

class ListItemNode(BaseModel):
    id: str
    level: int = 0                          # 多级列表
    blocks: List[BlockNode] = Field(default_factory=list)

class ListNode(BlockNode):
    type: Literal[BlockType.LIST] = BlockType.LIST
    ordered: bool = False
    items: List[ListItemNode] = Field(default_factory=list)

class ImageNode(BlockNode):
    type: Literal[BlockType.IMAGE] = BlockType.IMAGE
    image_id: str
    format: Optional[str] = None           # png/jpeg/svg/emf
    width_emu: Optional[int] = None
    height_emu: Optional[int] = None
    alt: Optional[str] = None
    data: Optional[bytes] = None           # 可选二进制

class FormulaNode(BlockNode):
    type: Literal[BlockType.FORMULA] = BlockType.FORMULA
    latex: Optional[str] = None
    text: Optional[str] = None

class TocNode(BlockNode):
    """可选导出的目录（D7）。默认不进 content，仅在 keep_toc 时输出。"""
    type: Literal[BlockType.TOC] = BlockType.TOC
    entries: List[Dict[str, Any]] = Field(default_factory=list)  # [{text, level, page?}]

# ============ 章节节点 ============

class SectionNode(BaseModel):
    id: str
    title: str
    level: int = Field(..., ge=0, le=9)     # 0 = 根
    heading_node_id: Optional[str] = None
    block_ids: List[str] = Field(default_factory=list)
    subsection_ids: List[str] = Field(default_factory=list)
    parent_id: Optional[str] = None

# ============ 文档元数据 ============

class DocumentMetadata(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    source_type: Optional[SourceType] = None
    source_file: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    page_count: Optional[int] = None       # 仅 PDF/OCR 有意义
    generator: Optional[str] = None        # 生成工具
    custom: Dict[str, Any] = Field(default_factory=dict)

# ============ 顶层文档 ============

class LogicalDocument(BaseModel):
    """规范 IR —— 所有 extractor 的统一输出。"""
    metadata: DocumentMetadata
    content: List[BlockNode]               # 扁平阅读序列
    section_tree: SectionNode              # 章节层级（根 level=0）
    block_to_section: Dict[str, str] = Field(default_factory=dict)  # block_id → section_id

    def get_block(self, block_id: str) -> Optional[BlockNode]: ...
    def get_section(self, section_id: str) -> Optional[SectionNode]: ...
```

### 4.3 序列化契约

- 规范输出 = `LogicalDocument` 的 JSON（`model_dump(exclude_none=True)`），节点以 `type` 判别。
- `content` 必须保持源阅读顺序（PDF/OCR 按 (page, y, x)；docx 按 `<w:body>` 顺序）。
- ID 在单文档内唯一且稳定，便于下游图遍历（`block_to_section`、`subsection_ids`、`parent_id`）。
- JSONL（兼容导出）：按页/按块分行，仅 `export` 模块产出，**不是**规范契约。

---

## 5. 各源 → IR 映射

| 源 | 前端 extractor | 关键映射 | provenance |
|---|---|---|---|
| **PDF（可编辑）** | PyMuPDF + span 管线（9 Stage）→ elements | heading→HeadingNode(level), paragraph→ParagraphNode, table→TableNode, list/image→对应 | page_index + bbox ✓（span→RunNode.provenance） |
| **PDF（扫描）/ 图片 / 复杂版式** | 远程 PaddleOCR 服务（PP-OCRv6/VL/Unlimited）→ markdown | markdown→IR：标题/表格/公式/图片/列表 → 对应节点（共享 markdown→IR 解析器） | `layoutParsingResults` 有 box 则带 bbox/page，否则 None |
| **DOCX** | lxml 直读 OpenXML | `outlineLvl`/pStyle→HeadingNode(1–9), `<w:p>`→ParagraphNode, `<w:tbl>`→TableNode(colspan/rowspan), 列表→ListNode, drawing→ImageNode；TOC 域识别→独立处理 | **无**（D6） |
| **XLSX**（未来） | openpyxl | sheet→SectionNode, 区域→TableNode/ParagraphNode | 无 |
| **PPTX**（未来） | python-pptx | slide→SectionNode(L1), 文本框→Heading/Paragraph, 表格/图片→对应 | slide_index |
| **HTML**（未来） | lxml/BeautifulSoup | `<h1-6>`→HeadingNode, `<p>`→ParagraphNode, `<table>`→TableNode, `<ul>/<ol>`→ListNode, `<img>`→ImageNode | 无 |

---

## 6. 模块边界与依赖方向

```
                    ┌──────────────────────────────┐
                    │   api  (parse() + FastAPI)   │
                    └──────────────┬───────────────┘
                                   ▼
                    ┌──────────────────────────────┐
                    │   orchestrator (源路由/调度)  │
                    └──────────────┬───────────────┘
             ┌─────────────────────┼─────────────────────┐
             ▼                     ▼                     ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │ pdf-extractor    │  │ docx-extractor   │  │ ocr-extractor    │
   │ (内含 pipeline)  │  │                  │  │ (服务+md→IR)    │
   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
            │                     │                     │
            └──────────┬──────────┴──────────┬──────────┘
                       ▼                     ▼
            ┌────────────────────┐  ┌────────────────────┐
            │   ir-model ★契约   │  │  structure-builder │
            │ (LogicalDocument)  │◄─┤ (章节树 + TOC 信号) │
            └─────────┬──────────┘  └────────────────────┘
                      ▲
            ┌─────────┴──────────┐
            │      export        │  (Markdown/JSON/PlainText/JSONL)
            └────────────────────┘
```

**依赖铁律**：
- 方向：`api → orchestrator → {extractors, structure-builder, export} → ir-model`。
- extractor 之间**禁止横向依赖**（D10）；span 管线（`pipeline` 包）只由 **pdf-extractor** 引用（OCR 不再用）；**markdown→IR 解析器**（`parsers.markdown`）由 ocr-extractor 与未来 html/markdown-extractor 共享。
- `ir-model` 是零依赖（仅 pydantic）的叶子契约，最先冻结、最稳定。

---

## 7. 迁移路线（来自 doc-paddle-ocr）

**保留并降格复用**（→ `pdf-extractor` / `pipeline` 内部）：
- 9 个 Stage（BodyAnalysis/Classification/TOCDetection/ImageDetection/LayoutFilter/TOCAnalysis/Merge/AutoLevel/PageNumberDetection）作为 span 管线核心。
- `heading_scorer` 评分体系、`normalize_font_size`、表格双引擎（pdfplumber+PyMuPDF）。
- SplitPipeline 分流机制（目录页/正文页）。

**丢弃**：
- 微服务脚手架（launcher/env_manager/8001–8004 服务、api/client HTTP 多服务）——D9。
- 死代码（main.py、model_catalog.py、未用 presets/函数）。
- 重复实现（`_table_to_markdown` ×3、`to_markdown` ×2、SERVICES ×2）。

**新增**：
- `ir-model`（全新，契约）。
- 各 extractor → IR 的映射层（pdf：span element → BlockNode；ocr：markdown → IR）。
- `structure-builder`（独立出章节树构建，原内嵌于管线）。
- `export`（统一导出）。
- `ocr-extractor`：远程 PaddleOCR 服务客户端（`OcrServiceClient`）+ 共享 `markdown→IR` 解析器（OCR 不复用 span 管线，见 D11）。

> 详细复用/重写清单见 `specs/pdf-extractor/spec.md` 的「复用边界」附录（任务 #5、#6 产出）。

---

## 8. 风险 / 权衡

| 风险 | 影响 | 缓解 |
|---|---|---|
| span 管线输出映射到 IR 时信息错配 | pdf-extractor 产出不符合契约 | ir-model spec 给出 element→BlockNode 逐字段映射表；Qoder 按表实现 |
| docx outlineLvl 缺失的文档（仅粗体大字号） | 标题识别率下降 | 启发式兜底（可配置），置信度低时标正文而非猜层级 |
| OCR 依赖远程服务可用性（D11） | 服务宕机 / 模型切换延迟 | `OcrServiceError` + 重试 + 超时 + 健康检查；模型未就绪时明确报错 |
| 双视图（content + section_tree）一致性 | 树与扁平序列不同步 | section_tree 由 content 单遍构建（structure-builder 唯一入口），禁止旁路修改 |
| 未来 xlsx/pptx 的「页/表」概念与树不完全契合 | extractor 映射需约定 | 现仅占位；落地时各写一份 mapping 约定，不改 ir-model 核心 |

---

## 9. 接口设计

### 9.1 库入口

```python
def parse(
    source: str | Path | bytes,
    *,
    source_type: SourceType | None = None,   # None = 按内容/扩展名自动判定
    keep_toc: bool = False,                   # D7：是否导出 TocNode
    extract_images: bool = True,
    options: ParseOptions | None = None,
) -> LogicalDocument: ...
```

### 9.2 HTTP

```
POST /parse
  multipart: file=<二进制>
  query: source_type?, keep_toc?, extract_images?
→ 200 { "document": <LogicalDocument JSON>, "markdown": "..." }
```

### 9.3 导出（export 模块）

```python
def to_markdown(doc: LogicalDocument, *, include_metadata: bool = False) -> str: ...
def to_json(doc: LogicalDocument, *, pretty: bool = True) -> str: ...      # 规范输出
def to_plain_text(doc: LogicalDocument) -> str: ...
def to_jsonl(doc: LogicalDocument) -> str: ...                             # 兼容旧接口
```

### 9.4 调试与可视化（debug 模块）

- **管线追踪**：`Pipeline(debug_dir=...)` 每 Stage 落盘 `{NN}_{name}.json`（schema 见 `specs/debug` §2；归属 `pipeline`，随 pdf-extractor 迁移）。
- **可视化**：`visualize(doc, source_path?, out_dir, ...)` 把 `LogicalDocument` 渲染为 **bbox 叠加图**（PDF 有页面底图；OCR 视服务 `layoutParsingResults`）或 **结构树**（docx / 无 bbox 的 OCR 结果）；`visualize_debug_dir(...)` 做**过程调试**（每 stage×page 一图 + 阶段对比图，复刻旧库，仅 PDF span 管线）。详见 `specs/debug/spec.md`。

---

## 10. 待确认（开放点）

| # | 问题 | 倾向 |
|---|---|---|
| O1 | docx 是否需要批注/修订/内容控件（B 原设计 P2/P3） | 默认 P3 不做，IR 预留节点类型；按需再开 |
| O2 | `ir-model` 是否由 Claude 先实现为可导入的 pydantic 包（供 Qoder import），还是只给 spec | 倾向 Claude 实现包（避免两方各自实现导致契约漂移） |
