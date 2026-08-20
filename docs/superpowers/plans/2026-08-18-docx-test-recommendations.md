# DOCX 阶段 A + B 测试建议

> 基于两阶段交付内容的代码审查，给出补充测试方向与优先级。
> 日期：2026-08-18

---

## 一、当前测试覆盖评估

### 已覆盖良好的区域

- **阶段 A**（DOCX 接入统一后处理）：DOCX postprocess 接线、字号比 doc_title、样式层级权威、栈首见精化、伪标题预扫描、`_merge_headings` 门控 — 在 `test_postprocess.py` 和 `test_docx.py` 中覆盖充分。
- **阶段 B**（OOXML 覆盖面扩展）：AlternateContent 去重、OMML 公式、OLE 占位、文本框内联展开、尾注/脚注、sdt 透明展开、页眉/媒体、红头公文组合 — 在 `test_docx_ooxml.py` 中 28 项测试覆盖。

### 覆盖薄弱 / 缺失的区域

见下文十项建议。

---

## 二、建议补充的测试场景

### 1. postprocess 提升路径的 ValidationError 风险（P1）

**背景**：阶段 B 遗留项。`_promote_doc_title_paragraphs_docx` 会把 `ParagraphNode` 替换为 `HeadingNode`，如果该段落同时含 `HyperlinkNode` / `InlineFormulaNode`（行内节点），`HeadingNode` 的 runs 类型校验可能触发 `ValidationError`。

**建议用例**：

- 居中大字号段落内嵌超链接 → postprocess 提升为 HeadingNode 后 runs 是否仍合法（`_run_only` 过滤是否生效）
- 居中大字号段落内嵌行内公式（`InlineFormulaNode`）→ 同上
- 类似场景在附件拆分边界处的表现（附件首段恰好被提升）

**目标文件**：`test_postprocess.py`

---

### 2. `_body_font_size` 众数计算的边界（P2）

**背景**：`_body_font_size`（`extractor.py` 第 32-51 行）遍历顶层段落 + 列表项内段落的全部 run 字号取众数，表格内文字不参与。

**建议用例**：

- **空文档**（无段落 / 纯表格文档）→ 返回 `None`，doc_title 提升路径全部 no-op，不崩
- **多种字号等频**（如 16pt 和 22pt 各出现 3 次）→ `Counter.most_common(1)` 的 tie-breaking 行为是否符合预期
- **单段落文档**（正文 = 标题本身）→ 基准字号等于标题字号，ratio = 1.0，居中时刚好触发提升

**目标文件**：`test_docx.py`

---

### 3. 尾注/脚注与附件拆分的交互（P1）

**背景**：`extractor.py` 第 160-166 行在 `postprocess` 之后追加尾注/脚注，注释说明"若在之前追加，split_attachments 会把尾注划进文档末尾的附件段"。但当前缺少端到端测试验证这一关键顺序。

**建议用例**：

- 文档末尾有附件标记段 + 尾注 → 尾注应在主文 content 末尾，不在附件段中
- 多个附件 + 尾注共存 → 尾注只出现在 `main_content`，不落入任何 `attachment` segment
- 尾注内容包含附件关键字（如"附表"）→ 不被 split_attachments 误切

**目标文件**：`test_docx.py` 或 `test_docx_ooxml.py`

---

### 4. 文本框 heading_source 契约（阶段 A/B 交叉点）（P2）

**背景**：`parser.py` 第 460-506 行 `_textbox_blocks` 为文本框内 heading 赋予 `heading_source` 和 `centered` metadata。calibrate_levels 的样式权威路径依赖 `heading_source == "style"` 判定。

**建议用例**：

- 文本框内带 outlineLvl 的标题 → metadata 含 `heading_source: "style"` + `textbox: True`
- 文本框内居中无样式标题 → metadata 含 `centered: True` + `textbox: True`
- 文本框标题进入 postprocess 后，calibrate_levels 样式权威路径正确识别

**目标文件**：`test_docx_ooxml.py`

---

### 5. OMML 公式的更多结构覆盖（P2）

**背景**：`embedded.py` 第 85-122 行 `omml_to_latex` 支持 `f`/`sSup`/`sSub`/`sSubSup`/`rad`/`d`，但当前测试只覆盖了 `f`/`sSup`/`rad`。

**建议用例**：

- `sSub`（下标）：`H₂O` → `H_{2}O`
- `sSubSup`（上下标同时）：`x₁²` → `x_{1}^{2}`
- `d`（括号定界符）：`(a+b)` → `(a+b)`
- 嵌套公式：`sSup` 内嵌 `f`（分数的平方）
- 未知 OMML 元素 → 递归文本拼接降级（不崩）

**目标文件**：`test_docx_ooxml.py`

---

### 6. 多行标题合并在 DOCX 路的显式锁死（P3）

**背景**：`_merge_headings` 门控条件（`postprocess.py` 第 448 行）是 `any(_prov_page(b) is not None)`，DOCX 块 provenance 全为 None → 不触发。

**建议用例**：

- **混合 provenance 场景**（防御性）：部分块有 provenance、部分无 → 确保不会误合无 provenance 的 DOCX 标题
- 该场景理论不该出现在生产中，但防御性测试确保代码路径安全

**目标文件**：`test_postprocess.py`

---

### 7. 真实公文样本端到端验收（P0 — 最重要）

**背景**：两个阶段都提到但尚未完成的人工验收项。

**执行方式**：运行 `scripts/spotcheck_docx_ooxml.py` 脚本，配合人工抽查 `spotcheck_out.txt`。

**重点观察**：

| 观察点 | 风险 | 阶段 |
|--------|------|------|
| 非居中加粗导语段（字号比 ≥ 1.2）| 误提升为 doc_title（历史教训） | A |
| 红头文本框位置 | 应在文档头部，无 Fallback 双份文字 | B |
| OLE 占位 | Excel/Equation 对象有 alt 描述 | B |
| 尾注位置 | 在文末、按 id 序排列 | B |
| 799 样本零崩溃 | 阶段 B 修复 27 类边界后是否保持 | B |
| 样式 H2 后无样式"一、"→ H3 | issues5 场景端到端工作 | A |

---

### 8. `_export_media` 的鲁棒性（P3）

**背景**：`extractor.py` 第 68-86 行 `_export_media` 落盘被引用媒体。

**建议用例**：

- `image_dir` 路径不可写（权限问题）→ WARN 跳过不崩（当前已有 try/except，验证日志输出）
- 同一 rId 被多个 ImageNode 引用 → 幂等写入（当前重复 write_bytes，结果正确但可验证）
- 媒体文件名含路径遍历（`../../etc/passwd`）→ 安全检查

**目标文件**：`test_docx_ooxml.py`

---

### 9. 页眉多 header 合并（P3）

**背景**：`extractor.py` 第 140-145 行收集全部 `word/header*.xml` 并拼接。

**建议用例**：

- 多个 header 文件（header1.xml, header2.xml）→ `header_text` 用 " / " 正确连接
- 空 header 文件（无文本）→ 不贡献多余分隔符
- 单 header 超 200 字符 → 截断到 200（`[:200]`）

**目标文件**：`test_docx_ooxml.py`

---

### 10. sdt 嵌套深度保护（P3）

**背景**：`parser.py` 第 203-213 行 `_iter_body_parts` 递归展开 sdt，depth 上限 10。

**建议用例**：

- 11 层 sdt 嵌套 → 第 11 层内容被截断，不崩
- sdt 内嵌 tbl（表格）→ 表格正确展开为 TableNode
- sdt 内嵌列表 → 列表正确分组

**目标文件**：`test_docx_ooxml.py`

---

## 三、回归测试优先级矩阵

| 优先级 | # | 测试类型 | 理由 |
|--------|---|---------|------|
| **P0** | 7 | 真实公文端到端验收 | 两阶段交付的最终质量门，不可替代 |
| **P1** | 1 | postprocess 提升 ValidationError | 阶段 B 遗留风险，可能导致运行时崩溃 |
| **P1** | 3 | 尾注 + 附件交互 | 关键顺序依赖，缺端到端测试 |
| **P2** | 2 | `_body_font_size` 边界 | 众数计算的极端输入 |
| **P2** | 4 | 文本框 heading_source 契约 | A/B 交叉点，接口契约需锁定 |
| **P2** | 5 | OMML 更多结构 | 覆盖面补全 |
| **P3** | 6 | 多行标题混合 provenance | 防御性，低概率 |
| **P3** | 8 | media 导出鲁棒性 | 防御性 |
| **P3** | 9 | 页眉多 header | 边界行为 |
| **P3** | 10 | sdt 嵌套深度 | 边界保护 |

---

## 四、测试文件归属

| 文件 | 补充项 |
|------|--------|
| `tests/test_postprocess.py` | #1, #2, #6 |
| `tests/test_docx.py` | #2, #3 |
| `tests/test_docx_ooxml.py` | #3, #4, #5, #8, #9, #10 |

不需要新建测试文件，在现有文件中追加即可。

---

## 五、前置条件

- P0（#7）需要你提供真实公文样本路径（之前提到"稍后给"）
- P1-P3 的测试可立即编写，全部使用手搓 XML fixture（与现有测试风格一致）
