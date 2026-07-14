# ocr-extractor — 扫描件/图片/复杂版式 → IR 行为契约

> 实现方：Claude（**重做**：原 span 版由 session ① 完成；现按 designs/001 D11 改为「远程服务 + markdown→IR」，见 SESSIONS.md 变更日志）
> 依赖：`document2chunk.ir`、`document2chunk.structure`、共享 `markdown→IR` 解析器
> IR 定义：`designs/001-target-architecture.md` §4
> 后端：远程 PaddleOCR 服务，见 `D:\project\server\PaddleOCR三件套使用文档.md`

## 1. 职责

把**扫描件 PDF / 图片 / 复杂版式 PDF / 长文档**解析为 `LogicalDocument`，`source_type=SourceType.OCR`。

后端 = **远程 PaddleOCR 服务**（PP-OCRv6 / PaddleOCR-VL / Unlimited-OCR），按输入选模型；解析服务返回的结构化 `markdown` 建 IR 树。

- **弃用本地 paddleocr 库**（D11）。
- **不走 span 管线**（span 管线只服务可编辑 PDF）；OCR 归入「结构化源」家族（与 docx/html 一致：解析结构化输出 → IR 树）。

**输入**：图片 / 扫描件或 mixed PDF / 复杂版式 PDF。
**输出**：`ExtractionResult`（`source_type=ocr`），由 api 调 `structure.assemble` 组装。

## 2. 处理流程

```
输入 → 选模型 → 调远程服务 HTTP API → 返回 {markdown, images, layoutParsingResults}
                                                      │
                                        markdown → IR（共享 markdown→IR 解析器）
                                                      │
                                  provenance 从 layoutParsingResults 取（若有）
                                                      ▼
                                             ExtractionResult → assemble → LogicalDocument
```

## 3. 模型选择

| 输入特征 | 模型 | 端点 |
|---|---|---|
| 长文档（页数 > 阈值，默认 >20） | Unlimited-OCR | `/api/unlimited-ocr` |
| 复杂版式 / 扫描件 / 表格·公式密集 | PaddleOCR-VL 1.6 | `/api/paddleocr-vl-1.6` |
| 规整公文 / 简单中文 | PP-OCRv6 | `/api/pp-ocrv6` |

- **必须**：模型可由 `options.ocr_model` 显式指定，覆盖启发式。
- **必须**：未指定时按启发式选（页数/版式复杂度），**默认 VL**（最通用）。
- **必须**：调用前确保目标模型 `active`：`GET /api/model-runtime`；若未就绪则 `POST /api/model-runtime/switch {"modelId": ...}`，轮询对应 `:808x/health` 的就绪态（首次加载 VL/Unlimited 较慢）。

## 4. 配置（禁止硬编码 token）

- **必须**：`endpoint`、`token`、默认模型、各端点路径、超时 由配置注入（环境变量 / config），**禁止**把 token 写进代码。
- **必须**：所有请求带 `Authorization: Bearer <token>`。

## 5. markdown → IR 映射（共享解析器）

复用一个**共享 markdown→IR 解析器**（`document2chunk.parsers.markdown`，未来 html/markdown-extractor 也复用）：

| markdown 元素 | → IR 节点 |
|---|---|
| `#..######` 标题 | `HeadingNode(level)` |
| GFM 管道表格 | `TableNode`（行/单元格） |
| `- `/`1. ` 列表（多级缩进） | `ListNode` / `ListItemNode` |
| `![alt](ref)` 图片 | `ImageNode`（`image_id=ref`；二进制从服务 `images` 取） |
| `$$..$$` / `$..$` 公式 | `FormulaNode(latex)` |
| 段落 / 其余文本 | `ParagraphNode` |

## 6. provenance

- **必须**：优先从服务 `layoutParsingResults` 取 `bbox` / `page_index` → `Provenance(source_type=ocr, page_index, bbox)`。
- **必须**：服务结果不含 box 时 → `provenance=None`（按流式文档处理，同 docx）。
- **必须**：低置信区域（若服务给 confidence）写入 `metadata={"low_confidence": True}`。

## 7. 需求

- **必须**：`scanned`/`mixed` PDF 经 `pdf_detect` 路由到本 extractor。
- **必须**：服务不可达 / 超时 / 模型未就绪 → 抛 `OcrServiceError`（不静默），支持重试与超时配置。
- **必须**：单文件解析失败 → WARN + 跳过 + 继续批量。
- **必须**：`extract_images=False` 时跳过图片二进制（`ImageNode.data=None`，仅留 `image_id`/alt）。
- **禁止**：引入本地 `paddleocr` / `paddle` 依赖。

## 8. 场景（When / Then）

- **当** 输入复杂版式 PDF **那么** 选 VL → 服务返回含表格/公式的 markdown → `TableNode`/`FormulaNode`。
- **当** 输入长文档（50 页）**那么** 选 Unlimited-OCR → 整本 markdown → 多页 IR。
- **当** 服务 `modelLoaded=false` **那么** 等待/切换 + 健康轮询，就绪后再解析。
- **当** 服务超时 **那么** 抛 `OcrServiceError`。
- **当** `layoutParsingResults` 含 box **那么** 节点带 `provenance.bbox`；否则 `provenance=None`。

## 9. 涉及实体

`OcrServiceClient`（HTTP + token + 模型切换 + 健康检查）、共享 `markdown→IR` 解析器、IR 节点。
