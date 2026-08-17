# 阶段 B 交接：DOCX OOXML 覆盖面补齐

> 日期：2026-08-17
> 性质：**自包含交接文档**——交给一个全新会话（无先前上下文）执行
> 前置：阶段 A（`2026-08-17-docx-postprocess-design.md`，分支
> `feat/docx-postprocess`）已合入 main 后才开工
> 本任务自己的 spec：开工时先走 brainstorming → 设计文档 → 实现计划流程，
> 本文是需求输入而非最终设计

## 1. 你要做什么

补齐 `document2chunk` 的 DOCX 解析器对 OOXML 的覆盖面：当前解析器只读
`word/document.xml` 的 body 顶层（段落/表格/图片/超链接），大量公文常见
元素未覆盖。你的任务是把这些元素解析进统一 IR。

**不是**重写解析器，也**不是**做后处理（阶段 A 已完成）。

## 2. 仓库导览（最小集）

- 本仓库：`D:\github\document2chunk`，面向 RAG 的多格式文档解析库。
  三路 extractor（PDF/OCR/DOCX）在 BlockNode 层汇合，共用
  `src/document2chunk/postprocess.py`（统一后处理：filter_noise →
  merge_cross_page → calibrate_levels → split_attachments）。
- 架构设计文档：`openspec/designs/009-unified-postprocess-architecture.md`
  （统一后处理）、`README.md`（模块结构与管线说明）。
- DOCX 相关代码（你工作的范围）：
  - `src/document2chunk/extractors/docx/extractor.py` —— 入口
    `DocxExtractor.extract()`，阶段 A 后内部已调 `postprocess()`
  - `src/document2chunk/extractors/docx/parser.py` —— document.xml →
    BlockNode；阶段 A 后带 `heading_source` 标记与伪标题预扫描
  - `src/document2chunk/extractors/docx/package_reader.py` —— zip part
    读取（目前只读 document/styles/numbering/core 四类）
  - `src/document2chunk/extractors/docx/styles.py` —— 样式注册与 rPr 合并
  - `src/document2chunk/extractors/docx/_ooxml.py` —— 命名空间/工具
  - `openspec/specs/docx-extractor/spec.md` —— 既有规格，改能力需同步
  - `tests/test_docx.py` + `tests/fixtures/` —— 测试与样本
- IR 模型：`src/document2chunk/ir/models.py`（加性扩展原则：不改已有
  节点定义，新增节点/可选字段）。

## 3. B 的范围

按以下优先级（先扫描后实施，见 §3.6）：

### 3.1 页眉/页脚 part（header*.xml / footer*.xml）

- 默认**不进正文**——DOCX 页眉页脚在独立 part，body 本就不含，效果等价
  于 PDF filter_noise，属「确认现状 + 锁测试」而非新功能。
- 可选增强：页眉中的文档标识信息进 `metadata.custom`（如公文红头机关名）。

### 3.2 脚注/尾注（footnotes.xml / endnotes.xml）

- 脚注引用出现在正文 run 中（`w:footnoteReference`）；脚注内容在独立
  part。方案需定：正文保留引用标记，脚注内容挂 IR 何处（建议：块级
  `metadata["footnotes"]` 或独立 FootnoteNode——设计时定，遵循加性扩展）。

### 3.3 文本框内容（`w:txbxContent`，含 VML `v:textbox` 与 DrawingML 两形态）

- 公文红头/大标题常用文本框承载，当前 `parser._parse_runs` 只遍历直接
  子元素，文本框内段落整体丢失。**优先级最高的内容补全**。
- 需定插入位置策略（文本框锚点段落处内联展开 vs 文档首部集中）。

### 3.4 OMML 公式（`m:oMath` / `m:oMathPara`）

- 映射到既有 `FormulaNode`（IR 已有该节点，PDF/OCR 路已在用）。
- OMML → LaTeX 的转换深度在设计时定（可用极简映射，纯文本兜底）。

### 3.5 嵌入对象（`w:object` / OLE）与图片二进制

- OLE 嵌入（Excel/Visio 等）：至少出占位 ImageNode + alt。
- 图片二进制导出：当前 `ImageNode` 只带 `image_id/format/尺寸`，媒体
  bytes 经 `package_reader.media_for_rel()` 可得但未落盘。对齐 PDF 路的
  `image_dir` 产物模式（见 `extractors/pdf.py` 的 `_attach_table_images`
  一节与 `extractors/_table_image.py`）。
- **决策点：DOCX 复杂表截图模式未接入**。`table_complex_format="image"`
  依赖 `attach_table_images`（PyMuPDF 渲染页面区域），docx 无法直接渲染，
  故 DOCX 复杂表实际只有 HTML 渲染一路（默认模式，`_has_merged_cells`
  分流在导出层、DOCX 已受益）。若要接入需前置 docx→PDF 转换
  （LibreOffice headless 一类），成本/收益在设计时评估；不接入则记录
  在案即可。

### 3.6 先扫描后实施（强制第一步）

写一个一次性脚本扫用户真实公文 docx 样本目录（路径开工时向用户要），
统计各特性出现频率（xslt/lxml 遍历 part 名 + 元素计数），按频率排定
实施顺序并写进你的设计文档。**不要凭直觉排优先级。**

## 4. 仓库工作约定

- **worktree 工作流**：新分支在独立 worktree 开发（本仓库惯例，见
  `git worktree list` 十余个）。用 superpowers 的 worktree 技能或
  `git worktree add` 均可，分支名建议 `feat/docx-ooxml-coverage`。
- **设计先行**：brainstorming → 设计文档（`docs/superpowers/specs/`，
  命名 `YYYY-MM-DD-<topic>-design.md`）→ 用户批准 → 实现计划 → TDD 实现。
- **TDD**：先写失败测试再实现（superpowers:test-driven-development）。
- **错误代号词表**：与用户沟通解析问题用 H/M/N/I/T/A/C/V 八类代号，
  定义在 `openspec/designs/011-error-classification.md`；报问题格式
  「代号+文件」。
- **规格同步**：能力变更同步 `openspec/specs/docx-extractor/spec.md`。
- **回归底线**：PDF/OCR 全量测试不变绿不合并；DOCX 既有 7 个测试不回退。

## 5. 与阶段 A 的边界

| 归属 | 内容 |
|---|---|
| 阶段 A（已完成，勿重做） | 统一后处理接入、heading_source 标记、伪标题预扫描、doc_title 字号比、附件拆分、同表头合并 |
| 阶段 B（你） | §3 全部 OOXML 覆盖面；扫描脚本；样本测试集扩充 |
| 共享但 B 需遵守 | `postprocess.py` 是三路共享代码，B 若需动它须跑全量回归 |

## 6. 验收标准

1. 扫描脚本产出真实样本特性频率表（进你的设计文档）。
2. 覆盖频率表中日出现（≥1 样本）的 §3 特性：单测 + 真实样本人工抽查。
3. 频率为零的特性：不实现，记录在案（YAGNI）。
4. `pytest` 全量绿；PDF/OCR 无回归。
5. `openspec/specs/docx-extractor/spec.md` 同步更新。

## 7. 风险与提示

- 公文 docx 多为 WPS/旧版 Word 产出，命名空间与 part 组织可能有怪癖——
  以真实样本为准，勿只按 ECMA-376 规范写。
- 文本框插入位置错误会把红头塞进正文中间——这是验收重点。
- 阶段 A 引入了 `heading_source` 标记契约（parser → calibrate_levels），
  你若改动 parser 的 heading 产出路径，保持该标记完整。
