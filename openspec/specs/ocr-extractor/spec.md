# ocr-extractor — 扫描件/图片/复杂版式 → IR 行为契约

> 实现方：Claude（feat/ocr worktree）
> 依赖：`document2chunk.ir`、`document2chunk.structure`、共享 `markdown→IR` 解析器
> IR 定义：`designs/001-target-architecture.md` §4
> 后端：远程 PaddleOCR 服务，见 `D:\project\server\PaddleOCR三件套使用文档.md`
> **响应 schema 经实测确认**（D:\temp\test.pdf / test2.pdf / 显存计算公式.pdf，2026-07-14）。

## 1. 职责

把**扫描件 PDF / 图片 / 复杂版式 PDF / 长文档**解析为 `LogicalDocument`，`source_type=SourceType.OCR`。

后端 = **远程 PaddleOCR 服务**（PP-OCRv6 / PaddleOCR-VL / Unlimited-OCR）。服务返回结构化 markdown + 逐块 bbox，解析成 IR 树。

- **弃用本地 paddleocr**（D11）；**不走 span 管线**（span 管线只服务可编辑 PDF）。
- **模型选择不写死**（澄清2 A1/A2）：先建**模型无关的共同层**（统一三模型输出），选择策略后置、可配。默认用 active 模型 / VL。

**输入**：图片 / 扫描件或 mixed PDF / 复杂版式 PDF。
**输出**：`ExtractionResult`（`source_type=ocr`），由 api 调 `structure.assemble` 组装。

## 2. 服务响应 schema（实测）

`POST /api/{pp-ocrv6|paddleocr-vl-1.6|unlimited-ocr}`（`Authorization: Bearer <token>`，multipart `file`）→ 200：

```jsonc
{
  "markdown": "<整本 GFM-ish markdown>",
  "images": { "<filename>": "<base64_png_str>", ... },          // 图片二进制（base64，无 data: 前缀）
  "layoutParsingResults": [                                      // 每页一项
    {
      "model": "Unlimited-OCR", "parser": "...",
      "page_count": N, "page_index": 1,                          // 1-based 页码
      "width": 1000, "height": 1000,
      "markdown": { "text": "<每页markdown>", "images": {filename: base64} },
      "metadata": { "fileType": 0, "imagesConfig": {...}, "rawMarkdown": "<|det|>label [x1,y1,x2,y2]<|/det|>text\n..." },
      "parsing_res_list": [
        { "block_label": "title|text|table|image|image_caption|formula|figure|page_number|header|footer|number",
          "block_order": 0, "block_content": "<文本或HTML>", "block_bbox": [x1,y1,x2,y2] }
      ]
    }
  ]
}
```

### 2.1 markdown 方言（实测）
- 标题：ATX `#`..`######`（→ heading level）。
- **表格：HTML `<table><tr><td>..</td></tr></table>`**（**非** GFM pipe；`parsing_res_list` 的 `table` 块 `block_content` 即此 HTML）。
- 图片：`![alt](filename)`，`filename` 是 `images` dict 的键。
- 列表：`- `（无序）、`1)`（有序，**非标准** `1.`，解析器须兼容）。
- 公式：服务宣称 KaTeX `$..$`/`$$..$$`（未实测到，按此兜底）。
- 段落：纯文本，空行分隔。

## 3. 处理流程（方案 A：markdown 建结构 + parsing_res_list 补 bbox）

```
扫描 PDF ──按页切分（PyMuPDF）──▶ 每页 1 页 PDF 子集
   │
   ▼ 对每页调 /api/<model>（active 模型；3 次重试；超时可配）
   ▼ 返回 {markdown, images, layoutParsingResults[0]}
   │
   ├─ 解析 markdown ──▶ IR 块（HeadingNode[level 来自 #]、TableNode[HTML]、ImageNode、ListNode、ParagraphNode、FormulaNode）
   ├─ parsing_res_list ──▶ 按 block_order 与 markdown 块 1:1 关联，取 block_bbox + page_index → provenance
   ├─ images[ref] base64 解码 ──▶ 落盘 p{page}_{idx}.png（edited-pdf 式），ImageNode.data=None（除非 extract_images）
   └─ 丢弃 page_number/header/footer 块
   │
   ▼ 汇总各页 content
ExtractionResult ──▶ structure.assemble ──▶ LogicalDocument
```

**输入形态（C7，实测定）**：unlimited-ocr 对 49 页整本返回 500（OOM/超时），单页 9.5s 正常 → **按页（或小批）送，不整份发长 PDF**。`page_count` 由 api 透传（澄清2 A3）。

### 3.1 块标签 → IR 节点
| `block_label` | → IR | 备注 |
|---|---|---|
| `title` | `HeadingNode` | level 由 markdown `#` 关联（同 order） |
| `text` | `ParagraphNode` | |
| `table` | `TableNode` | 解析 `block_content` 的 HTML `<table>` |
| `image` / `figure` | `ImageNode` | image_id=markdown ref；二进制从 `images` 取 |
| `image_caption` | `ParagraphNode` | 作为图片说明段落 |
| `formula` | `FormulaNode` | latex/text |
| `page_number`/`header`/`footer`/`number` | （丢弃） | 不进 content |

### 3.2 标题层级
`parsing_res_list` 的 `title` 块**不含 level**。level 取自 markdown：按 `block_order` 把 `title` 块与 markdown 中 `#`/`##` 标题逐个对应（实测两者同序同内容）。无法对应时降级为 H1。

## 4. 模型选择（不写死）

- **必须**：`options.ocr_model` 可显式指定（`vl`/`pp-ocrv6`/`unlimited`），覆盖一切。
- **必须**：未指定时用 active 模型；若需切换，调 `POST /api/model-runtime/switch`（单 GPU，切换全局、慢）。
- **单 GPU 并发（澄清2 B4/B5）**：模型未 active 时实时调用失败 → **产中间结果**（落盘每页响应），待目标模型激活后批量处理；不自动阻塞切换 thrash。库内对模型切换**加锁串行**（避免多请求互踩）。
- 选择策略（长文档→Unlimited / 复杂→VL / 公文→PP-OCRv6）**后置研究**，不写死（澄清2 A1/A2）。

## 5. provenance

- **必须**：`provenance = Provenance(source_type="ocr", page_index=<0-based，由 layoutParsingResults.page_index(1-based) 转 0-based>, bbox=block_bbox)`。
- **必须**：bbox 不可得（关联失败）时 `provenance.page_index` 仍给（页已知），`bbox=None`。
- 无 confidence 字段（服务不返回）→ 不映射。

## 6. 配置（env，澄清2 G19/G20）

- `DOCUMENT2CHUNK_OCR_ENDPOINT`（默认 `http://128.23.67.112:8000`）
- `DOCUMENT2CHUNK_OCR_TOKEN`（**禁止入库**）
- `DOCUMENT2CHUNK_OCR_MODEL`（默认 `vl`）
- `DOCUMENT2CHUNK_OCR_TIMEOUT`（默认 180s）
- `DOCUMENT2CHUNK_OCR_MAX_RETRIES`（默认 3，澄清2 H21）

## 7. 需求

- **必须**：`scanned`/`mixed` PDF 经 `pdf_detect` 路由到本 extractor；`page_count` 由 api 透传。
- **必须**：mixed PDF 页级路由（只送 scanned 页）**优先级低、后补**（澄清2 C8）；首版整份按页送。
- **必须**：按页切分送服务（长 PDF 整份 500）。
- **必须**：服务不可达/超时/模型未就绪 → `OcrServiceError`（含状态码/模型名/建议）；3 次指数退避重试（超时/5xx 重试，4xx 不重试）。
- **必须**：`extract_images=False` 时 `ImageNode.data=None`；True 时按 edited-pdf 方式落盘 `out_dir/images/p{page}_{idx}.{ext}`，`data=None`（路径式引用）。
- **必须**：HTML `<table>` 用 lxml/html 解析成 `TableNode`（行/单元格，平铺无合并——HTML 表格 colspan/rowspan 视情况保留）。
- **禁止**：引入本地 `paddleocr`/`paddle`。

## 8. 场景（When / Then）

- **当** 输入 49 页扫描 PDF **那么** 按页切分逐页调服务（不整份），汇总成多页 IR。
- **当** 某页含表格 **那么** markdown 出 `<table>` → `TableNode`，带该 table 块 bbox。
- **当** 某页含图片 **那么** `![](ocr_images/..png)` → `ImageNode`，二进制 base64 解码落盘。
- **当** 服务 500/超时 **那么** 重试至 3 次，仍失败抛 `OcrServiceError`。
- **当** 目标模型未 active **那么** 产中间结果（落盘），不阻塞；记录待激活后处理。
- **当** `title` 块无对应 markdown `#` **那么** 降级为 H1。

## 9. 涉及实体

`OcrServiceClient`（httpx + token + 模型切换锁 + 健康检查 + 3 次重试）、`PageChunker`（PyMuPDF 按页切）、共享 `markdown→IR` 解析器（ATX 标题 + HTML 表格 + 图片 + 列表 + 公式）、`images` 落盘、IR 节点。
