# document2chunk

> 面向 RAG 的多格式文档解析库——将 PDF、DOCX、扫描件/图片转换为统一的结构化文档树中间表示（IR），供下游语义切片、向量化、知识图谱使用。

## 核心特性

- **多格式统一解析**：支持可编辑 PDF（PyMuPDF + pdfplumber 双引擎）、扫描件/图片（远程 PaddleOCR 三模型）、DOCX（lxml 直读 OpenXML）
- **类型化文档树 IR**：输出 `LogicalDocument`，包含三维结构——章节树（`section_tree`）、扁平内容流（`content`）、块到章节映射（`block_to_section`），并携带 `provenance` 出处信息（页码/bbox/置信度）
- **智能后处理**：跨页页眉页脚/页码移除、跨页段落续接、多行标题合并、栈式自适应标题定级、附件边界拆分
- **5-Stage Pipeline**：PDF 前端线性管线（BodyAnalysis → ImageDetection → Classification → TOCDetection → MergeStage），自动区分全局/局部 Stage
- **多格式导出**：Markdown / JSON(AST) / PlainText / JSONL
- **可选 HTTP 服务**：FastAPI `/parse` 端点，支持 multipart 和 raw body 上传
- **模块化按需安装**：pdf / ocr / docx / api / viz / dev 可选依赖分组

## 安装

```bash
# 基础安装（仅 IR 模型）
pip install document2chunk

# 按需安装功能模块
pip install document2chunk[pdf]    # PDF 解析（PyMuPDF + pdfplumber）
pip install document2chunk[ocr]    # OCR 解析（httpx + markdown-it-py）
pip install document2chunk[docx]   # DOCX 解析（lxml）
pip install document2chunk[api]    # FastAPI HTTP 服务
pip install document2chunk[viz]    # 可视化（Pillow）
pip install document2chunk[dev]    # 开发测试（pytest）

# 安装全部功能
pip install document2chunk[pdf,ocr,docx,api]
```

要求 Python ≥ 3.10。

## 快速开始

### 库调用

```python
import document2chunk

# 统一入口——自动根据文件扩展名/魔数路由到对应 extractor
doc = document2chunk.parse("report.pdf")

# 显式指定源类型
doc = document2chunk.parse("scan.png", source_type="ocr")

# 解析选项
doc = document2chunk.parse(
    "document.docx",
    keep_toc=True,        # 保留目录节点
    extract_images=True,   # 提取图片信息
)
```

### 访问文档结构

```python
from document2chunk import LogicalDocument

doc: LogicalDocument = document2chunk.parse("report.pdf")

# 元数据
print(doc.metadata.title, doc.metadata.source_type)

# 遍历章节树
for section in doc.iter_sections():
    print(f"{'  ' * section.level}{section.title}")

# 遍历所有内容块
for block in doc.iter_blocks():
    if hasattr(block, "text"):
        print(block.type, block.text[:80])

# 按 ID 查找块或章节
block = doc.get_block("block_000042")
section = doc.get_section("sec_000003")
```

### 导出

```python
from document2chunk.export import to_markdown, to_json, to_plain_text, to_jsonl

markdown = to_markdown(doc)          # 按章节树递归输出 Markdown
json_str = to_json(doc)             # 规范 JSON（可往返 model_validate_json）
plain = to_plain_text(doc)          # 纯文本（扁平 content）
jsonl = to_jsonl(doc)               # 每行一个块
```

### HTTP 服务

```bash
# 启动 FastAPI 服务
python -m document2chunk.api --host 127.0.0.1 --port 8000

# 或通过 uvicorn
uvicorn "document2chunk.api:create_app" --factory
```

```bash
# 调用 /parse
curl -X POST http://127.0.0.1:8000/parse \
  -F "file=@report.pdf"

# 健康检查
curl http://127.0.0.1:8000/health
```

### OCR 服务配置

OCR 解析需要远程 PaddleOCR 服务，通过环境变量或 `.env` 文件配置：

```bash
# 复制示例配置
cp .env.example .env

# 编辑 .env 填入真实 token
DOCUMENT2CHUNK_OCR_TOKEN=your-token-here
DOCUMENT2CHUNK_OCR_ENDPOINT=http://128.23.67.112:8000

# 可选配置
DOCUMENT2CHUNK_OCR_MODEL=vl           # vl | pp-ocrv6 | unlimited
DOCUMENT2CHUNK_OCR_TIMEOUT=180        # 超时（秒）
DOCUMENT2CHUNK_OCR_MAX_RETRIES=3      # 重试次数
```

## 架构

```
源文件 ─→ 源路由（扩展名/魔数） ─→ Extractor ─→ ExtractionResult ─→ structure.assemble() ─→ LogicalDocument
                                      │
                          ┌───────────┼───────────┐
                          ▼           ▼           ▼
                    PdfExtractor  OcrExtractor  DocxExtractor
                     (PyMuPDF +    (PaddleOCR    (lxml 直读
                      pdfplumber    远程服务)      OpenXML)
                      + Pipeline)
```

### 模块结构

| 模块 | 职责 |
|---|---|
| `ir` | 规范 IR 模型（Pydantic v2 判别联合）：`LogicalDocument`、`BlockNode`、`SectionNode`、`Provenance` 等 |
| `api` | 统一入口 `parse()` + 源路由 + FastAPI HTTP 服务 |
| `extractors.pdf` | 可编辑 PDF 提取（PyMuPDF span + pdfplumber 表格双引擎） |
| `extractors.ocr` | 扫描件/图片提取（远程 PaddleOCR → markdown → IR） |
| `extractors.docx` | DOCX 提取（lxml 直读 OpenXML → AST → IR） |
| `pipeline` | 5-Stage 线性管线引擎（仅 PDF 前端使用） |
| `pipeline.stages` | Stage 实现：BodyAnalysis / Classification / ImageDetection / TOCDetection / Merge |
| `structure` | 章节树构建（栈算法）+ `ExtractionResult` → `LogicalDocument` 组装 |
| `postprocess` | 全文档后处理：噪声过滤 / 跨页合并 / 标题定级 / 附件拆分（PDF / OCR / DOCX 三路共用） |
| `export` | 多格式导出：Markdown / JSON / PlainText / JSONL |

### IR 模型

`LogicalDocument` 是规范中间表示，所有 extractor 统一输出：

- **`content`**：扁平阅读序列（`HeadingNode` / `ParagraphNode` / `TableNode` / `ListNode` / `ImageNode` / `FormulaNode` / `TocNode`）
- **`section_tree`**：嵌套章节层级（`SectionNode` 树，根 level=0）
- **`block_to_section`**：block_id → section_id 映射
- **`metadata`**：文档元数据（title / author / source_type / page_count 等）
- **`attachments`**：拆分出的附件文档

块节点支持递归嵌套（表格单元格、列表项内可含任意块），每个节点可选携带 `Provenance`（page_index / bbox / confidence）。

### PDF 5-Stage Pipeline

PDF 前端使用线性管线，编排引擎 `Pipeline` 按 Stage 的 `is_global` 属性自动分组为连续段，交替执行全局（跨页合并）和局部（逐页）两组 Stage：

```
BodyAnalysis → ImageDetection → Classification → TOCDetection → MergeStage
  (global)        (local)         (local)          (local)        (local)
```

#### 1. BodyAnalysisStage（全局）

正文基准分析。遍历所有页元素的 span 层，统计 `(font, size)` 对应的字符数，取众数作为正文基准字体和字号，写入 `PipelineContext.body_font` / `body_font_size`，供下游 Classification 等 Stage 使用。本 Stage 透传元素不做任何修改。

#### 2. ImageDetectionStage（局部，逐页）

图片检测与占位符替换。从 `PipelineContext.image_infos` 读取每页图片信息，通过三信号分层判定图片真实类型：

- **span 文字存在性**（主裁判）：图 bbox 内有 ≥3 个可编辑文字元素 → 背景/文字叠层 → 保留文字
- **page_coverage**：图占页面 >50% 且无文字 → 全页背景
- **layout label**（可选）：有版面分析数据时，用 layout 标签裁决

仅**真 figure** 的图片区域替换为 `type=image` 占位符元素；背景/装饰图保留原有文字不做替换。重叠的 image 占位符通过 Union-Find 合并。

#### 3. ClassificationStage（局部，逐页）

元素分类——判定每行是 heading 还是 paragraph。采用**多信号综合评分**（非串级），三个并行信号叠加：

| 信号 | 规则 | 评分 |
|---|---|---|
| 字号比值 | 元素字号 vs 正文基准字号的比值，映射到 H1–H4 | 按比值分层赋值 |
| 编号模式 | 中文编号（`第X章`/`一、`/`（一）`）或数字编号（`1.1`/`3.2.1`），按 depth 分层；「编号后无句号正文」= 纯标题强信号，「编号后接正文」= 混合弱信号 | 0.20–0.65 |
| 独立成行 | 同行无其他元素 + 宽度 <65% 页宽 + 不在页眉页脚区 | 0.20 |

综合评分 ≥ 0.50 → heading（`type=title/heading`，`level` 由字号或编号 depth 决定）；否则 → paragraph。评分结果记录在元素的 `heading_confidence` 和 `heading_level_conf_history` 中，支持调试追溯。

#### 4. TOCDetectionStage（局部，逐页）

目录页识别。检测策略：

1. 扫描每个元素的文本是否含**点线引导符**（`...` / `……` / `·····` / 行尾 `..`）
2. 一页中 ≥3 个连续元素含点线 → 判定为目录页
3. 目录页上的元素标记：
   - 「目录」/「目  录」/「Table of Contents」→ `type=toc_title`
   - 含点线的条目 → `type=toc_entry`
4. 合并同行的孤立章节号（如 PDF 提取时「3.2.1」与后面的标题+页码被拆成两个元素，通过 y 坐标接近 + x 间距合理判断为同行，合并为一个 toc_entry）

toc_entry 不参与后续 MergeStage 的段落合并。

#### 5. MergeStage（局部，逐页）

段落合并——将连续的行级 paragraph 元素合并为段落级元素。合并条件（全部满足才合并）：

- 类型都是 paragraph（heading/title 只在同行 y 差 ≤5pt 且同级时合并）
- level 相同
- 字号差 ≤ 0.5pt
- 字体名称完全相同
- 垂直间距 ≤ 标准行间距 × 1.5（标准行间距 = 相邻同款 paragraph 间距的众数，按 0.1pt 网格聚类）
- 下一元素不以列表/编号标记开头（`1.`/`2、`/`一、`/`（一）` 等 → 新段落/列表项，不合并；用 `(?!\d)` 排除小数如 `1.5亿元`）

合并时拼接文本/markdown/spans，更新 bbox 为外接矩形，传播低置信标记。

### 文档级后处理（postprocess）

Pipeline 产出的页级元素经 `elements_to_blocks()` 映射为 `BlockNode` 后，进入三路（PDF / OCR / DOCX）共用的全文档后处理，按以下顺序执行：

1. **`filter_noise`** — 跨页页眉/页脚/页码移除，三证据分层（强→弱）：
   - layout 强证据：版面框标了 header/footer/number → 中心点落入即移除
   - 跨页重复：顶/底带内文本（数字归一化）在 ≥3 页同一位置出现 → 移除
   - 页码序列：底部 + 同行较窄 + 纯数字/N/M，形成跨页递增序列 → 移除
   - 绝不盲删顶/底 N%

2. **`merge_cross_page`** — 跨页段落续接 + 多行标题合并：
   - 段落续接：page N 末段 + page N+1 首段，前者不以句尾结束符结尾 + 后者不以列表标记开头
   - 多行标题合并：连续无编号 heading 且同级 + 前者无句尾 → 拼接（最多 4 对）

3. **`calibrate_levels`** — 栈式自适应标题定级：
   - doc_title 检测与提升（OCR 按高度比 ≥1.8 / edited-PDF 按居中 + 高度比 ≥1.2）
   - 主标题 → `metadata.title` + H1，其余竞争性 doc_title 降级为 Paragraph
   - 栈序自适应定级：首次出现的编号样式自动分配高层级
   - toc_entries 精确/前缀匹配覆盖栈序定级
   - 附件/附录边界重置层级栈

4. **`split_attachments`** — 附件边界拆分：
   - 检测附表/附件/附录标题边界（不限于 HeadingNode，段落级编号也匹配）
   - 输出 `(正文, [附件1, 附件2, ...])`

DOCX 路同样汇入统一后处理：无页概念使 `filter_noise` / `merge_cross_page` 天然
no-op；`calibrate_levels` 对 DOCX 使用字号比 doc_title 检测（run 自带磅值），
样式标题层级权威、无样式编号段落经伪标题预扫描后由栈式定级；`split_attachments`
与 `merge_split_tables` 照常生效。

## 开发

```bash
# 克隆并安装开发环境
git clone <repo>
cd document2chunk
pip install -e ".[pdf,ocr,docx,dev]"

# 运行测试
pytest

# 带覆盖率
pytest --cov=document2chunk
```

### 异常体系

所有异常继承 `Document2ChunkError`：

| 异常 | 场景 |
|---|---|
| `UnsupportedFormatError` | 输入格式不受支持 / 路由失败 |
| `MissingDependencyError` | 可选依赖缺失 |
| `InvalidSourceError` | 源文件损坏 / 缺失关键部分 |
| `ExtractionError` | 提取过程异常 |
| `PipelineError` | 管线编排异常 |

### 扩展新格式

实现 `Extractor` 协议即可接入：

```python
from document2chunk.ir import ExtractionResult, SourceType

class MyExtractor:
    source_type: SourceType = SourceType.HTML  # 未来支持

    def extract(self, source, *, options=None) -> ExtractionResult:
        # 解析源文件 → 产出 BlockNode 列表 + metadata
        ...
```

通过 `register_extractor()` 注入：

```python
from document2chunk.api import register_extractor
register_extractor(SourceType.HTML, MyExtractor())
```

## 设计原则

1. **逻辑结构优先**：目标是文章的章节归属，而非像素级版面还原
2. **结构与出处分离**：内容层级源无关，bbox/页码作为可选 `provenance` 元数据
3. **extractor 解耦**：各格式独立 extractor，禁止横向依赖，只依赖 `ir-model`
4. **单体库 + 可选 HTTP**：不引入布局引擎
5. **加性扩展**：新格式/新功能不修改已有 IR 节点定义

## 许可证

MIT
