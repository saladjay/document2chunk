# ocr-extractor — 扫描件/图片 → IR 行为契约

> 实现方：Claude
> 依赖：`document2chunk.ir`、`document2chunk.pipeline`、`document2chunk.structure`
> IR 定义：`designs/001-target-architecture.md` §4
> 参考：`doc-paddle-ocr/pdf_parsers/pipeline/extractors.py: OcrSpanExtractor`、`refraction2/docx_ocr_span_adaptation_survey.md` §四

## 1. 职责

把**扫描件 PDF / 图片**解析为 `LogicalDocument`，`source_type=SourceType.OCR`。用 PaddleOCR + 版面分析，缺失 font/bold 信息时降级。

**输入**：图片路径，或扫描件 PDF（先转图片）。
**输出**：`LogicalDocument`，节点带 `provenance`（bbox + page_index + confidence）。

## 2. 处理流程

```
扫描件 PDF → PyMuPDF 逐页渲染为图片（DPI≈200）/ 图片直接输入
          → PaddleOCR 识别文本行（bbox + text + confidence）
          → 版面分析（PP-DocLayout：text/title/figure/table/footer 区域标签）
          → span（bbox + 估算字号 + 区域标签）
          → 复用 pipeline（source_type 感知降级）
          → element → BlockNode 映射（同 pdf-extractor §4）
          → structure-builder
          → LogicalDocument
```

## 3. 需求

- **必须**：每个节点 `provenance` 含 `source_type="ocr"`、`page_index`、`bbox`、`confidence`。
- **必须**：字号估算 = `bbox 高度 × (72 / DPI)`；正文基准取所有文字区域高度的众数。
- **必须**：标题识别采用**降级策略**——版面分析标签 `title` → `HeadingNode`（主信号），估算字号高于正文基准（次信号）；**bold 判断失效**（flags 恒 0），AutoLevel 降低 bold 权重或跳过。
- **必须**：`confidence < 阈值`（默认 0.5）的行**标记低置信**但不跳过（写入 `metadata`）。
- **必须**：表格/图片区域由版面分析标签 `table`/`figure` 识别 → `TableNode`/`ImageNode`。
- **必须**：`page_number`/页眉页脚（版面标签 `footer`）不进 `content`。
- **必须**：pipeline 的 `BodyAnalysis`/`Classification`/`AutoLevel` 支持 `source_type` 感知（OCR 用 bbox 高度估正文基准，不依赖 font）。
- **必须**：`scanned`/`mixed` PDF 经 `pdf_detect` 路由到本 extractor。

## 4. 场景（When / Then）

- **当** 版面分析标某区域为 `title` **那么** 产出 `HeadingNode`。
- **当** 某 OCR 行 `confidence=0.3` **那么** 节点保留，`metadata={"low_confidence": True}`。
- **当** 版面标签为 `footer` **那么** 不进入 `content`。
- **当** 输入是扫描件 PDF（多页） **那么** 每页 `page_index` 递增，`provenance.page_index` 正确。
- **当** OCR 某页失败 **那么** WARN + 该页跳过 + 继续后续页。

## 5. 涉及实体

`PaddleOCRFrontend`（识别+版面分析）、`pipeline` 各 Stage（source 感知版）、IR 节点（带 OCR provenance）。
