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
         · mc:AlternateContent 只走 mc:Choice（WPS 双写去重）
         · 文本框 txbxContent → 锚点段后内联展开（metadata["textbox"]）
         · m:oMath/oMathPara → InlineFormulaNode/FormulaNode
         · w:object(OLE) → 占位 ImageNode（ProgID alt）
         · 尾注/脚注 → 引用标记 + 文末内容块（metadata["note"]）
         · w:sdt → 透明展开 sdtContent
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
- **必须**：`mc:AlternateContent` 双写去重（只走 `mc:Choice`，无 Choice 走 `mc:Fallback`），文字/图片不双计。
- **必须**：文本框 `w:txbxContent`（wps/VML 两形态）→ 锚点段落之后内联展开，块带 `metadata["textbox"]=true`；框内图片随展开，不泄出顶层。
- **必须**：OMML `m:oMath`→`InlineFormulaNode`、`m:oMathPara`→`FormulaNode`（极简 LaTeX 映射：frac/上下标/sqrt/括号，纯文本兜底）。
- **必须**：OLE `w:object` → 占位 `ImageNode`（`alt="OLE 对象 (ProgID)"`、预览图 `r:id`、v:style pt→EMU）。
- **必须**：尾注/脚注引用 → run 文本追加 `[尾注N]`/`[脚注N]`；内容 part（跳过 separator）→ 文末集中块，`metadata["note"]={"type","id"}`，按 id 数值序。
- **必须**：`w:sdt` → 透明展开 `sdtContent`（TOC 容器走既有 TOC 消费），嵌套上限 10 层。
- **必须**：`image_dir` 提供时仅落盘被引用媒体（zip 内原名）；页眉非空文本 → `metadata.custom["docx"]["header_text"]`（截断 200 字）；页眉页脚内容不进 `content`（§3.5 禁止项指不进正文/不模拟版面，与 metadata 记录不冲突）。

### 3.5 provenance 与版面（D6）

- **必须**：所有 docx 节点 `provenance=None`。
- **禁止**：为 docx 计算/模拟 bbox、页码、页眉、页脚。

### 3.6 TOC

- **必须**：识别 TOC（`<w:fldSimple instr="TOC">` / SDT / `<w:instrText>` 含 "TOC"）→ 走独立流程：条目作信号消费（校准标题层级），默认不进 `content`；`keep_toc=True` 时聚合 `TocNode`。

### 3.7 高级特性

- 批注（`comments.xml`）、修订（`<w:ins>`/`<w:del>`）：**默认不实现**（799 样本各约 1%，见阶段 B 扫描）；内容控件（`<w:sdt>`）已实现透明展开（§3.4）。

### 3.8 错误恢复

- 单段落/表格解析失败 → WARN + 跳过 + 继续。
- 文件 > 上限 → `FileTooLargeError`。

### 3.9 统一后处理（2026-08-17 阶段 A）

`DocxExtractor.extract()` 内部调用 `document2chunk.postprocess.postprocess()`（与 PDF / OCR 两路共用）：

- `filter_noise` / `merge_cross_page`：DOCX 无页概念，天然 no-op
- `calibrate_levels`：doc_title 按字号比（居中 + ≥基准，或 ≥基准×1.2；段落提升仅在无标题级候选时触发）；样式标题（outlineLvl/pStyle）层级权威；无样式短编号段落由 parser 预扫为伪标题（`heading_source="heuristic"`），栈式定级（首见样式从栈顶+1 分配）
- `split_attachments`：附件/附录边界拆分为 `attachments`（各段 `custom={"is_attachment": True}`）
- `merge_split_tables`：连续同表头表格合并

## 4. 场景（When / Then）

- **当** 段落含 `<w:outlineLvl w:val="2"/>` **那么** 产出 `HeadingNode(level=3)`。
- **当** `<w:pStyle w:val="MyH1"/>` 且 `MyH1` 继承链→`Heading1` **那么** 产出 `HeadingNode(level=1)`。
- **当** Run 引用字符样式 `MyCode`(字体 Consolas) + 直接字号 14pt，段落样式字号 16pt **那么** `RunNode.style.font="Consolas"`、`font_size=14.0`。
- **当** 表格含 `gridSpan=2` **那么** 对应 `TableCellNode.colspan=2`。
- **当** 文档含 TOC 域 **那么** 默认 `content` 不含目录条目，标题层级被其校准。
- **当** 序列化 `LogicalDocument` **那么** docx 节点 JSON 中无 `provenance` 字段（exclude_none）。
- **当** 段落含 WPS 双写 AlternateContent（Choice/Fallback 各一份文本）**那么** 文本只出一份（取 Choice）。
- **当** 红头文本框锚定首段 **那么** 其内段落紧随首段展开且 `metadata["textbox"]=true`。
- **当** run 含 `<w:endnoteReference w:id="3"/>` 且 endnotes.xml 有 id=3 条目 **那么** 正文含 `[尾注3]`，文末出现 `[尾注3] {内容}` 块。
- **当** run 含 `<w:object>` + ProgID=Excel.Sheet.8 **那么** 产出 `ImageNode(alt="OLE 对象 (Excel.Sheet.8)")`。

## 5. 涉及实体

`PackageReader`（zipfile+缓存）、`StyleRegistry`/`StyleDefinition`、`DocumentParser`（含 `ParagraphParser`/`TableParser`/`ListParser`/`ImageExtractor`）、IR 全部块/行内节点。
