# docx-extractor — DOCX → IR 行为契约

> 实现方：Claude
> 依赖：`document2chunk.ir`、`document2chunk.structure`（structure-builder）
> IR 定义：`designs/001-target-architecture.md` §4
> 参考：`doc-paddle-ocr/document-to-chunk/{PRD,SDD,SRS,technical-spec}-Word.md`（lxml/AST 原设计）

## 1. 职责

把 `.docx` 解析为 `LogicalDocument`，`source_type=SourceType.DOCX`。用 **lxml 直读 OpenXML**（ECMA-376），不走版面、不算 bbox。

**输入**：`.docx` 文件路径（或 bytes）。
**输出**：`LogicalDocument`，所有节点 `provenance=None`。

## 2. 处理流程

```
.docx → zipfile 解压 → 读取 document.xml / styles.xml / docProps/core.xml / numbering.xml
     → StyleRegistry（解析 styles.xml，构建 basedOn 继承链 + 缓存）
     → DocumentParser 遍历 <w:body>：
         · <w:p> → ParagraphNode / HeadingNode（标题检测）
         · <w:tbl> → TableNode（gridSpan/vMerge 合并）
         · 列表（numId/ilvl）→ ListNode
         · <w:drawing> → ImageNode
         · <w:hyperlink> → HyperlinkNode
         · <w:r> → RunNode（样式经 StyleRegistry 解析）
     → TOC 域识别 → 独立处理（信号消费 + 可选 TocNode）
     → structure-builder 构建章节树
     → LogicalDocument
```

## 3. 需求

### 3.1 解析基础

- **必须**：用 `lxml`（`recover=True` 处理畸形 XML）+ 标准库 `zipfile`。
- **禁止**：使用 `python-docx`（覆盖不全、不解析继承链、增依赖）。
- **必须**：`document.xml` 缺失/损坏 → 抛 `InvalidDocxError`（fast fail）；`styles.xml` 缺失 → 用默认样式降级。

### 3.2 标题识别（优先级）

- **优先级 1**：`<w:outlineLvl w:val="N"/>`（N=0–8）→ `level = N+1`（1–9）。
- **优先级 2**：`<w:pStyle>` 的 `basedOn` 继承链根为 `Heading1`–`Heading9` → 对应 level；含中文样式名（"标题 1"）按名匹配。
- **优先级 3**（可选，需配置启用）：启发式（`^第[一二三...]+章`→H1、`^\d+\.\d+`→H2 等）。
- **必须**：无上述标记 → `ParagraphNode`（正文），`is_heading=False`。

### 3.3 样式继承链

- **必须**：`StyleRegistry` 解析 `basedOn` 图，合并优先级：`直接格式化 > 字符样式(rStyle) > 段落样式(pStyle) > basedOn 链 > docDefaults`。
- **必须**：循环继承检测 → 截断 + WARN。
- **必须**：RunNode.style 字段（font/font_size/bold/italic/...）取解析后真实值。

### 3.4 结构元素

- **必须**：表格 `<w:tbl>` → `TableNode`；`gridSpan`→`colspan`、`vMerge`→`rowspan`；单元格内可嵌套段落/列表/子表格。
- **必须**：列表 → `ListNode`（`ordered`、多级 `ilvl`→`level`、编号格式）。
- **必须**：图片 `<w:drawing>` → `ImageNode`（`image_id`=r:embed、`format`、`width_emu/height_emu`、`alt`）；二进制 `data` 默认不填。
- **必须**：`<w:hyperlink>` → `HyperlinkNode`（外部 `r:id` / 内部 `w:anchor`）。

### 3.5 provenance 与版面（D6）

- **必须**：所有 docx 节点 `provenance=None`。
- **禁止**：为 docx 计算/模拟 bbox、页码、页眉、页脚。

### 3.6 TOC

- **必须**：识别 TOC（`<w:fldSimple instr="TOC">` / SDT / `<w:instrText>` 含 "TOC"）→ 走独立流程：条目作信号消费（校准标题层级），默认不进 `content`；`keep_toc=True` 时聚合 `TocNode`。

### 3.7 高级特性（P3，默认不做）

- 批注（`comments.xml`）、修订（`<w:ins>`/`<w:del>`）、内容控件（`<w:sdt>`）：**默认不实现**；ir-model 已预留节点类型，按需再开。

### 3.8 错误恢复

- 单段落/表格解析失败 → WARN + 跳过 + 继续。
- 文件 > 上限 → `FileTooLargeError`。

## 4. 场景（When / Then）

- **当** 段落含 `<w:outlineLvl w:val="2"/>` **那么** 产出 `HeadingNode(level=3)`。
- **当** `<w:pStyle w:val="MyH1"/>` 且 `MyH1` 继承链→`Heading1` **那么** 产出 `HeadingNode(level=1)`。
- **当** Run 引用字符样式 `MyCode`(字体 Consolas) + 直接字号 14pt，段落样式字号 16pt **那么** `RunNode.style.font="Consolas"`、`font_size=14.0`。
- **当** 表格含 `gridSpan=2` **那么** 对应 `TableCellNode.colspan=2`。
- **当** 文档含 TOC 域 **那么** 默认 `content` 不含目录条目，标题层级被其校准。
- **当** 序列化 `LogicalDocument` **那么** docx 节点 JSON 中无 `provenance` 字段（exclude_none）。

## 5. 涉及实体

`PackageReader`（zipfile+缓存）、`StyleRegistry`/`StyleDefinition`、`DocumentParser`（含 `ParagraphParser`/`TableParser`/`ListParser`/`ImageExtractor`）、IR 全部块/行内节点。
