# 设计 008 — 远程表格识别服务集成（table-extractor）

> 状态：**提议（设计稿，待实现）**
> 分支：`feat/table-recognition`
> 服务：`table-rec-api`（PaddleOCR 通用表格识别 v2 产线 + 单模块），经 pandocr-web `http://128.23.67.112:8000` 代理，**纯 CPU**，warm ~20-30s/份
> 服务文档：`D:\project\server\表格识别API调用文档.md`、`表格识别API使用文档.md`
> 上线：2026-07-17

---

## 1. 上下文与动机

现有表格处理弱：

| 来源 | 现状 | 缺陷 |
|---|---|---|
| **可编辑 PDF** | `extractors/pdf.py` 用 pdfplumber + PyMuPDF `find_tables()` → `table_rows` → `_mapping._table_to_table_node` | 线条启发式，复杂表/无线表易漏；`colspan/rowspan` 丢失（当前全摊平为 1×1 单元格）；封面排版误判（designs/004 §3.2 已加 table 校验缓解） |
| **OCR（扫描/图片）** | 远程 PaddleOCR（VL/unlimited）返回 markdown，`_markdown.py` 解析 `<table>`/GFM | 结构受限于 OCR 模型的表格还原能力；合并单元格在 md 里被摊平 |

新服务 `/api/table-recognition`（端点 4，主用）对**任意 PDF/图片**做：版面定位表格 → 有线/无线分类 → 对应权重（SLANeXt + RT-DETR）→ 结构 + 单元格 + OCR 填字 → 输出 **HTML（含 `colspan`/`rowspan` + 文字）+ 每单元格 OCR 置信度 + 可选 xlsx/md/json + 单元格框**。质量显著高于现有两条路径，且 **`colspan`/`rowspan` 直接可用**（ir-model 的 `TableCellNode` 早有这两个字段，现有映射没填）。

## 2. 目标 / 非目标

**目标**
- G1 新增 `table-extractor` 能力：任意 PDF/图片 → 高质量 `TableNode`（合并单元格、文字、provenance）。
- G2 `HTML <table> → TableNode` 解析器，**保留 `colspan`/`rowspan`**（补全现有映射缺的合并信息）。
- G3 作为现有 pdf/ocr 路径的**表格升级**（`enhance_tables`）：用服务结果替换弱表。
- G4 复用 OCR 服务的客户端/配置范式（httpx、token 走环境变量、`.env` 自动加载、可注入测试）。

**非目标**
- N1 不替代可编辑 PDF 的全文 span 提取（表格之外的正文仍走 pdf-extractor）。
- N2 不改 ir-model（`TableNode`/`TableCellNode` 字段已够；`colspan`/`rowspan` 现有就支持）。
- N3 暂不做异步任务队列（服务同步，单份 900s 上限；超大 PDF 拆页留 follow-up）。

## 3. 架构

```
                ┌─────────────────────────────────────┐
   PDF/图片 ───▶│ TableExtractor.extract(source)       │
                │   ├─ TableServiceClient.recognize()  │──▶ POST /api/table-recognition
                │   │     {tables:[{page,html,ocr,      │      (pandocr-web :8000, Bearer)
                │   │      json?,md?,xlsx?}]}           │
                │   ├─ _html_parser.html_to_table_node()│──▶ TableNode(rows/cells,
                │   │     colspan/rowspan + 文字 + 置信度│      colspan/rowspan 保留)
                │   └─ provenance(page_index, cell box) │
                └──────────────┬──────────────────────┘
                               ▼ ExtractionResult(content=[TableNode...],
                                                   metadata.source_type=pdf|ocr)
            （集成模式）replace pdf/ocr 结果里的弱表（按 page+bbox 匹配）
```

### 模块布局（新增 `extractors/table/` 子包）

```
extractors/table/
  __init__.py          # 导出 TableExtractor / TableServiceClient
  _client.py           # TableServiceClient（httpx + token + fmt/page_range/超时/重试）
  _html_parser.py      # HTML <table> → TableNode（stdlib html.parser，无依赖）
  extractor.py         # TableExtractor.extract → ExtractionResult
  _config.py           # TableConfig（env：DOCUMENT2CHUNK_TABLE_TOKEN/ENDPOINT…）
```

## 4. HTML → TableNode 映射（核心）

服务 `tables[].html` 形如：
```html
<table><tbody>
  <tr><td colspan="2">合并表头</td><td>列C</td></tr>
  <tr><td rowspan="2">a</td><td>b</td><td>c</td></tr>
  <tr><td>b2</td><td>c2</td></tr>
</tbody></table>
```

用 stdlib `html.parser.HTMLParser`（不引 BeautifulSoup，零依赖）解析：

| HTML | → IR |
|---|---|
| `<table>` | 一个 `TableNode` |
| `<tr>` | `TableRowNode`（`is_header`：该行全 `<th>` 或为首行，可配） |
| `<td>`/`<th>` | `TableCellNode(colspan, rowspan)`（读属性，默认 1） |
| 单元格文字 | `TableCellNode.blocks = [ParagraphNode(text=...)]` |
| `tables[].page` | `TableNode.provenance.page_index` |
| `json.cell_box_list`（可选） | 单元格框 → 内层 `ParagraphNode.provenance.bbox`（`TableCellNode` 本身无 provenance 字段） |
| `ocr.rec_scores` | 低置信（< 阈值，默认 0.5）单元格 → `metadata={"low_confidence": True}`（同 OCR 约定） |

> 关键收益：**`colspan`/`rowspan` 终于进 IR**（现有 `_mapping._table_to_table_node` 全填 1，丢失合并）。本解析器按属性如实填 `TableCellNode.colspan/rowspan`。

## 5. TableServiceClient

```python
class TableServiceClient:
    def recognize(self, data: bytes, filename: str, *,
                  fmt: str = "html,json",      # html 必给；json 给 cell_box_list + rec_scores
                  page_range: str = "all") -> dict:
        # POST /api/table-recognition，multipart file；返回 {tables, count, formats}
```

- 配置：`TableConfig.from_env()`（`DOCUMENT2CHUNK_TABLE_TOKEN` / `_ENDPOINT` / `_TIMEOUT` / `_FMT`）+ `.env` 自动加载（复用 OCR 的 `_load_dotenv` 机制）。token 与 pandocr-web 共用（同 OCR token）。
- 所有请求带 `Authorization: Bearer <token>`。
- **错误**：HTTP ≥400 / 超时 / 不可达 → `OcrServiceError`（复用，或新增 `TableServiceError`）。
- **冷启动**：首次（或容器重启后首请求）下模型 ~几分钟，可能 504/上游超时 → 文档建议**重试**。client 内置 1 次自动重试 + 长超时（默认 900s，对齐端点 4 上限）。
- `http_client` 可注入（`httpx.MockTransport` 单测，同 OCR client）。

## 6. TableExtractor

```python
class TableExtractor:
    source_type = ...  # 按输入推断：PDF 魔数 → SourceType.PDF；图片 → SourceType.OCR
    def extract(self, source, *, options=None) -> ExtractionResult:
        # 1. 读 bytes
        # 2. client.recognize(fmt="html,json")
        # 3. 每个 table：_html_parser.html_to_table_node(t["html"], page=t["page"], ...)
        # 4. metadata（source_type、page_count、generator="paddleocr-table"）
```

- 输出 `ExtractionResult.content = [TableNode...]`（仅表，无正文）。
- `options.table_fmt` / `options.page_range` / `options.extract_xlsx`（是否要 xlsx 落盘）透传。

## 7. 集成模式（升级 pdf/ocr 的表）

两种用法（设计都支持，实现分阶段）：

**7.1 独立调用**（最简）：直接 `TableExtractor.extract(pdf)` 拿所有高质量表。
**7.2 enhance_tables 升级**（集成）：`PdfExtractor`/`OcrExtractor` 加 `enhance_tables=True` 选项：
1. 先正常提取（含弱表）。
2. 对有表的页（或整份）调 `TableExtractor` 拿强表。
3. 按 `(page_index, bbox 交并)` 匹配，用强表**替换**弱表；未匹配的强表追加、无强表覆盖的弱表保留。

> 升级匹配用方向性包含（同 designs/004，非 IOU），避免大表/小表错配。

## 8. provenance / 坐标

- `TableNode.provenance`：`page_index`（来自 `tables[].page`）；`bbox` 取 `json.html`/`cell_box_list` 的并集或首个 cell box（best-effort）。
- 单元格框 `cell_box_list`：服务坐标系为**渲染图像素**（200 DPI 默认渲染）；需换算到源自然坐标（PDF 点 / 图片像素）。复用 `_chunker` 的页面尺寸或 OCR 的 1000 归一化校准思路（待联调确认服务坐标空间后定）。
- 源输入为图片 → bbox 留在像素空间（自然）；PDF → 校准到页面点。

## 9. 降级与错误

| 情况 | 处理 |
|---|---|
| 服务不可达 / 超时 / 500 | 抛 `TableServiceError`（独立调用）；`enhance_tables` 模式下 WARN + 保留原弱表（不阻断主流程） |
| 冷启动 504 | client 内置 1 次重试（带退避） |
| 无表格（`count=0`） | 返回空 content（正常） |
| `enhance_tables` 未启用 / 服务未配置 token | 走现有 pdf/ocr 表格路径（完全向后兼容） |

## 10. 配置（禁硬编码 token）

```ini
# .env（已 gitignore）
DOCUMENT2CHUNK_TABLE_TOKEN=06mPxXt3...        # 与 pandocr-web / OCR 共用
DOCUMENT2CHUNK_TABLE_ENDPOINT=http://128.23.67.112:8000
DOCUMENT2CHUNK_TABLE_TIMEOUT=900
DOCUMENT2CHUNK_TABLE_FMT=html,json
```

复用 `_load_dotenv`（OCR 已实现），`TableConfig.from_env()` 先加载 `.env`。

## 11. 测试

- `_html_parser` 单测（构造 HTML 含 colspan/rowspan → 断言 `TableCellNode.colspan/rowspan`、文字、is_header）。**零服务依赖**。
- `TableServiceClient` 用 `httpx.MockTransport`（同 OCR client 测试）：断言鉴权头、端点、fmt 透传、HTTP 错误抛 `TableServiceError`、504 重试。
- `TableExtractor` 用 stub client（返回固定 `{tables}`）→ 断言 `TableNode` 数量、provenance.page_index、合并单元格。
- 联调（可选，需内网+token）：真实 PDF → 服务 → `TableNode`，验证 colspan/rowspan 与 xlsx 解码。

## 12. 落地顺序（建议）

1. **`_html_parser`（HTML→TableNode，含 colspan/rowspan）+ 单测** —— 核心新逻辑、零依赖、可先做。
2. **`TableServiceClient` + `_config`（.env）+ MockTransport 测试**。
3. **`TableExtractor`（独立调用）+ stub 测试**。
4. **集成 `enhance_tables`（pdf/ocr 升级模式）+ 真实服务联调**。

## 13. Follow-up / 开放点

- 服务坐标空间（cell_box_list 像素 vs 归一化）需联调确认（同 OCR provenance 修复经验）。
- 超大 PDF：服务同步 900s 上限 → 拆页/异步队列（后续）。
- 是否把 `enhance_tables` 做成 `parse()` 的默认（表格密集文档自动升级）——看精度与延迟权衡。
- 单模块端点（classification/structure/cells）是否暴露给 debug 可视化（叠加 cell box）。

## 14. 几何重建（`cell_box_list`）——复杂合并表头的补救

**动机**：服务返回的 `html` 在**超复杂多级合并表头**上拓扑错乱——colspan 落在空格、
文字塞进 1 宽格、列数算错（实测「永久基本农田划定情况表」21 列被算成 20）。但 `html`
的**每格文字本身是对的**，错的只是拓扑。而 `json.cell_box_list`（单元格检测框）**几何可靠**，
合并信息藏在尺寸里（高框=rowspan、宽框=colspan）。

**方案**：`_geo_reconstruct.py` 用几何还原真实网格拓扑，文字从 html 按行对齐；不可靠时
`try_geo_to_table_node` 返回 `None`，extractor 透明回退 `html_to_table_node`。

**算法 6 步**：
1. **自适应边聚类** `cluster_edges`（贪心 + 滑动均值；`tol = max(8, 0.18×median(box 维度))`）。
2. **幻影带修复** `_repair_phantom_bands`：迭代合并间距 `< max(10, 0.45×median 带间距)` 的
   相邻带（同一线的检测抖动）——比固定 tol 稳，tol 略小导致同线被拆成两簇时合回。
3. **框→span** `_snap_to_band`（bisect 最近带线）→ 每个 box 得 `(r0,c0,rowspan,colspan)`。
4. **覆盖校验** `validate_grid`：`holes`/`overlaps` 超 `max(2, 5%×R×C)` 判失败（留余量，真实检测不完美）。
5. **文字行对齐** `align_texts`：html 行 ↔ geo 行（按 `r0` 分组），行内非空 html 文字 zip 到
   geo 格（`c0` 序）。尊重模型的行结构（行边界大致可信），跳过行内空格/空数据行。
6. **构造 TableNode**：cell bbox 挂**内层** `ParagraphNode.provenance.bbox`（`TableCellNode` 无
   provenance 字段）；`TableNode.provenance.bbox` 默认取所有 box 并集。

**5 个回退条件**（`try_geo_to_table_node` 返回 None）：① `cell_boxes` 缺失/<2；② 网格退化；
③ 覆盖校验不过；④ 文字数 > 格数；⑤ 任何异常。**关键**：校验全过后才 build（消耗 `idc`），
故与 html 回退共用同一 `_Idc` 时 ID 不断号。回退**逐表**独立。

**`table_reconstruct` 模式**（extractor option，默认 `auto`）：
- `auto`：有 `cell_box_list` 就试 geo，失败回退 html。
- `geo`：强求几何（仍带静默回退）。
- `html`：逃生口，直走 html（跳过 geo）。

**表头判定** `_decide_header`：`header_rows` 显式覆盖优先（表单表建议传，如 5）；其次首行；
其次「整行全合并」自动。

**实测**（`自然资规2019-1号`）：
- p19「永久基本农田划定情况表」：geo 重建 **8 行 × 21 列**（html 算成 20 列），左列通栏
  rowspan、表头 colspan、通栏注释全部正确归位；r3 13 个叶子列名、r4 栏1–栏9、r6 注释文字
  全部正确。相较原 html 质变。
- p20：geo 检测到 40 处覆盖重叠 → 判自身不可靠 → 回退 html（优雅降级）。
- p30：geo 成功（11×9）。

**已知限制**（v1）：
- **文字归属在「合并格与其宽表头共享起始行」时会错位**：当 rowspan 格（如左列通栏）与
  宽 colspan 表头同处一行（r0 相同），行内 zip 可能把表头文字塞进 rowspan 格。根因：html
  文字**无 box**、且其 span 已与服务拓扑脱钩，没有信号把文字钉到「宽格」而非旁边的「高格」。
  只有带 box 的 OCR（`dt_polys`）才能根治。网格拓扑与 span 仍正确；典型表 95%+ 文字落位正确。
- **容差需多 PDF 调参**：`_TOL_FRAC=0.18`、`_PHANTOM_FRAC=0.45` 仅在一张表验证；已参数化
  `row_tol`/`col_tol`，待更多 PDF 到位后扫参。
- `rec_scores` 占位未启用（无 box、不对齐单元格，字符串匹配太脆）。

**模块**：`_geo_reconstruct.py`（`geo_to_table_node` 硬失败 / `try_geo_to_table_node` 守护）；
导出于 `extractors/table/__init__.py`；测试 `tests/test_table_geo_reconstruct.py`（26 例 + 真机快照）。

---

*参考：`表格识别API调用文档.md`（端点 1-4、响应字段、有线/无线分支、运维/回滚）、`表格识别API使用文档.md`（轻量指南）。*
