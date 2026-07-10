# api — 库入口与 HTTP 行为契约

> 实现方：Claude
> 依赖：全部 extractor、`structure`、`export`、`ir`
> 架构：`designs/001-target-architecture.md` §9

## 1. 职责

提供统一入口 `parse()`：源路由 → 选 extractor → structure-builder → 返回 `LogicalDocument`。另提供 FastAPI `/parse` HTTP 端点。

## 2. 库入口

```python
def parse(
    source: str | Path | bytes,
    *,
    source_type: SourceType | None = None,   # None = 自动判定
    keep_toc: bool = False,
    extract_images: bool = True,
    options: ParseOptions | None = None,
) -> LogicalDocument: ...
```

## 3. 需求

### 3.1 源路由（source_type 为 None 时自动判定）

| 输入特征 | 路由到 |
|---|---|
| `.pdf` + `pdf_detect` 判 editable | `pdf-extractor` |
| `.pdf` + scanned/mixed | `ocr-extractor` |
| `.docx` | `docx-extractor` |
| 图片（png/jpg/...） | `ocr-extractor` |
| 其他 | 抛 `UnsupportedFormatError` |

- **必须**：显式 `source_type` 优先于自动判定。
- **必须**：PDF 用 `pdf_detect`（editable≥70% 页阈值）区分 pdf/ocr 路线。

### 3.2 调度

- **必须**：extractor 产出 `content`（含 heading level）+ 可选 TOC 条目 → 调 `structure-builder.build(...)` 填充 `section_tree`/`block_to_section`（+ `keep_toc` 的 `TocNode`）→ 返回 `LogicalDocument`。
- **必须**：`extract_images=False` 时跳过图片二进制提取（`ImageNode.data` 为 None）。
- **必须**：`metadata.source_file` 记录输入路径/名。

### 3.3 错误

- **必须**：不支持的格式 → `UnsupportedFormatError`。
- **必须**：文件损坏/缺失关键部分 → 对应 extractor 抛明确异常（`InvalidDocxError`/`InvalidPdfError`），不静默吞错。
- **必须**：可选依赖（PyMuPDF/PaddleOCR/lxml）缺失 → 提示安装对应 extra（`pip install document2chunk[pdf]` 等）。

## 4. HTTP（FastAPI）

```
POST /parse
  multipart: file=<二进制>
  query:     source_type?, keep_toc?, extract_images?
→ 200 { "document": <LogicalDocument JSON>, "markdown": "..." }
→ 400 UnsupportedFormatError
→ 422 InvalidDocxError / InvalidPdfError
```

- **必须**：单进程单体服务，**不**做微服务（D9）。
- **必须**：响应同时返回规范 `document`（JSON）与便捷 `markdown`。
- **必须**：`GET /health` → `{status:"ok", version}`。

## 5. 场景（When / Then）

- **当** `parse("a.pdf")` 且 a.pdf 为 editable **那么** 返回 `source_type="pdf"` 的 `LogicalDocument`。
- **当** `parse("a.pdf")` 且 a.pdf 为 scanned **那么** 路由 ocr-extractor，`source_type="ocr"`。
- **当** `parse("a.docx")` **那么** 路由 docx-extractor，所有节点 `provenance=None`。
- **当** `parse(b"x", source_type="html")` **那么** 未来 html-extractor（当前抛 `NotImplementedError` 或 `UnsupportedFormatError`）。
- **当** `POST /parse` 上传 docx **那么** 返回 `document` + `markdown`。

## 6. 涉及实体

`parse()`、`ParseOptions`、各 extractor、`structure-builder`、`export`、FastAPI app。
