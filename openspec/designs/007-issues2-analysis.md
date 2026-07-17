# issues2 根因分析（designs/007）

> 逐条对照 issues2.md，归类到根因 + 修复方向 + 中间过程信息缺口。

---

## 根因分类总览

| # | 根因 | 影响条数 | 已修? | 需补中间记录? |
|---|---|---|---|---|
| R1 | `join_cross_page_paragraphs` 条件过严/未覆盖 | 12+ | 🟡 部分 | ✅ |
| R2 | OCR 大标题→metadata（决策1=B），用户期望 H1 | 10+ | ❌ 需讨论 | — |
| R3 | viz 少画最上方块（header 遮挡/bbox 缺失） | 8 | ❌ | ✅ |
| R4 | 页码过滤不完整（递增/非标准格式/OCR 路径无检测） | 10+ | ❌ | ✅ |
| R5 | 图片 markdown 变成 HTML `<div><img>` 格式 | 5+ | ❌ | — |
| R6 | 附件未拆分为独立 output | 6+ | ❌ | — |
| R7 | OCR 把图片/盖章标成 text→ParagraphNode | 5+ | ❌ | ✅ |
| R8 | edited-PDF 无编号居中标题未识别 | 5+ | 🟡 部分 | ✅ |
| R9 | LayoutFilter 固定百分比删正文 | 4+ | 🟡 后处理补 | — |
| R10 | MergeStage 合并过激 | 3+ | 🟡 ratio 调了 | ✅ |
| R11 | 首页误判表格（HTML 样式 PDF） | 3 | 🟡 另一 session 部分修 | — |
| R12 | OCR 错别字 | 3+ | ❌ 服务限制 | — |
| R13 | 表格合并单元格/专用模型 | 5+ | ❌ Phase 3 | — |
| R14 | span 排序/同行字体不一信息缺失 | 3+ | ❌ | ✅ |
| R15 | 跨页列表序号错 | 2+ | ❌ | ✅ |
| R16 | OCR 公式误识别（`$ ^{**} $`） | 1 | ❌ | — |
| R17 | 重复识别字符 | 1 | ❌ | ✅ |

---

## 逐条根因分析

### R1：跨页段落换行符未合并（12+ 条）

**涉及**：2017.13号#4, 2024.204号#2/#6, 2024.59号#5, 2019.1号#4, 2024.12.25#2, 2021.行动方案#2, 2022.1086#2, 2026.38号#2, 2019.11号#2, 2021.1号#4, 2016.16号#4

**根因**：`join_cross_page_paragraphs()` 的条件 `_is_cross_page_continuation`：
1. `p2.page_index > p1.page_index`——OCR 路线的 page_index 在 `build_page_blocks` 中设置为 `page_index` 参数（0-based，来自 `iter_pages`）。**应该正确**。
2. `t[-1] ∉ _SENTENCE_END_CHARS`——中文段落末尾常有 `。！？` 但也可能以 `；` `：` 或无标点结尾。**条件可能过严**：以 `；` 结尾的也该合并（分号后继续是同段）。
3. **另一个可能**：OCR 路线每页独立处理，`build_page_blocks` 只处理单页 → **跨页的两个段落不是相邻 BlockNode**（中间可能有该页的其他块隔开）。`join_cross_page_paragraphs` 只检查 `content[i]` 和 `content[i+1]`（紧邻），但跨页续接的两个段落在 content 中可能不相邻（如果中间有其他块）。

**最可能原因**：条件 3——跨页的"page N 最后一个 paragraph"和"page N+1 第一个 paragraph"在 content 中**不一定相邻**（中间可能隔了图片/表格/标题等块）。

**修复**：
- 放宽 `_is_cross_page_continuation`：不看紧邻，而是找 page N 的最后一个 ParagraphNode + page N+1 的第一个 ParagraphNode（不管中间隔什么）。
- 放宽句号检查：加入 `；` `：` `、` 等也可继续的标点。

**中间过程缺口**：当前 `intermediate/page_NNN_response.json` 有服务原始响应，但没有**join 决策日志**（为什么没 join / 哪两个块应该 join）。
→ **修补**：在 `join_cross_page_paragraphs` 里加 log（"page N 末段 'XXX' + page N+1 首段 'YYY' → join/skip, reason: ZZZ"），写到一个 `intermediate/join_log.json`。

---

### R2：OCR 大标题→metadata，用户期望 H1（10+ 条）

**涉及**：2017.13号#1, 2024.204号#1, 2023.1843号#2, 2024.59号#3, 2019.1号#2, 2024.944号#1, 2016.2号#1, 2018.30号#1, 2026.38号#1, 2019.11号#1, 2021.1号#1/#2, 2023.68号#3, 2016.16号#1

**根因**：决策 1=B（大标题→metadata.title，不进 heading 池）。用户现在反馈：**文章标题应该是 heading level 1**（在 markdown 里有 `# 标题`），不是隐藏在 metadata 里。

**根因深层**：OCR 服务的 parsing_res_list 把文档标题标为 `title`（无 level），calibrate 的 `DOC_TITLE_RATIO > 1.8` 判为大标题→metadata。但用户想要标题做 H1（结构可见）。

**修复方向**：改决策 1=B→1=A（大标题做 H1，章节做 H2）或混合（大标题同时做 metadata.title + H1）。需与用户确认。

---

### R3：可视化少画最上方块（8 条）

**涉及**：2017.13号#3, 2024.204号#4, 2024.59号#4, 2019.1号(隐含), 2016.2号#4, 2018.30号#3, 2022.129号#3, 2016.16号#3

**根因**：`debug/_annotate.py draw_annotations` 在顶部画了一个 header 栏（`_HEADER_HEIGHT`，约 28px），header 背景（半透明白色）覆盖了页面顶部的第一个块的 bbox。如果第一个块的 bbox y 坐标小（靠近页面顶部），它的框被 header 栏遮住。

**另一个可能**：OCR 路线的第一个块的 provenance.bbox 为 None（bbox 关联失败）→ draw_annotations 跳过（`if not bbox: continue`）。

**修复**：
- 检查第一个块的 bbox 是否有效（查 intermediate）。
- header 栏改为透明或缩小不遮挡。
- 或第一个块在 header 下方重新绘制。

**中间过程缺口**：intermediate 里没记录**每个块的 provenance.bbox 是否有效**（关联成功/失败）。
→ **修补**：在 `build_page_blocks` 里对关联失败的块记 log（"element order=X, bbox关联失败, content='YYY'"），写到 `intermediate/mapping_log.json`。

---

### R4：页码过滤不完整（10+ 条）

**涉及**：2023.1843号#3, 2024.59号#2, 2019.1号#1, 2025.17号#3, 2016.2号#2, 2019.11号#3, 2022.129号#2, 2023.89号#2, 2016.16号#2

**根因**：
- **OCR 路线**：`filter_cross_page_noise` 只查重复文本；页码递增（321/322/323）每页不同 → 不被检测。`parsing_res_list` 里的 `page_number` 标签的块**已在映射层丢弃**（DROP_LABELS），但如果 OCR 服务没标 `page_number`（标成了 `text`）→ 混入 content。
- **edited-PDF 路线**：`PageNumberDetection` 用 5 条正则（`^\d+$`、`^第\s*\d+\s*页$`、`^\d+\s*/\s*\d+$`、`^Page\s+\d+`、`^P\.?\s*\d+`）+ ≥70% 页面命中。但某些页码格式（如 "321"、"322" 纯数字）如果不在底部或不到 70% → 漏网。

**修复**：
- OCR：加正则后处理——底部位置的纯数字/页码格式块 → 丢弃。
- edited-PDF：放宽 PageNumberDetection 正则 + 按位置（底部）+ 序列性（递增数字）检测。

**中间过程缺口**：没记录页码检测的中间结果（哪些块被判为页码、哪些漏判）。
→ **修补**：在 PageNumberDetection + filter_cross_page_noise 里 dump 检测详情。

---

### R5：图片 markdown 变成 HTML `<div><img>` 格式（5+ 条）

**涉及**：2024.59号#1, 2019.1号#3, 2019.5.4#5, 2023.68号#2, 2026.38号(隐含)

**根因**：当前 `to_markdown` 对 ImageNode 输出 `![alt](image_id)`（GFM 格式）。但用户看到的是 `<div style="text-align: center;"><img src="..." />`（HTML）。这说明**另一个 session 改了 `export/_helpers.py` 的 ImageNode 渲染逻辑**——从 GFM `![]()` 改成了 HTML `<div><img>`。

**验证**：检查当前 `_helpers.py block_markdown` 对 ImageNode 的输出。我上一次看到的是 `f"![{alt}]({block.image_id})"`（GFM）。但另一个 session 可能改了它。

**修复**：确保 ImageNode → GFM `![alt](image_id)`，不要 HTML `<div><img>`。用户明确说"不要引入 html 格式，字符过多影响 embedding"。

---

### R6：附件未拆分独立 output（6+ 条）

**涉及**：2023.1843号#1/#4, 2024.59号#6, 2019.1号#4, 2016.2号#7, 2016.16号#6

**根因**：calibrate 有附录重置（RE_APPENDIX → prev_level=0），但没**拆分**为独立 output。用户期望附件是单独的 `output_附件.md`（因为附件的标题层级与正文独立）。

**修复**：新增附件拆分 pass——检测附件边界（附表/附件/附录）→ 拆分 content 为 [正文, 附件1, 附件2...]，每个独立 assemble + 导出。或者：在 assemble 里用附件边界拆分 section_tree。

---

### R7：OCR 把图片/盖章标成 text→ParagraphNode（5+ 条）

**涉及**：2024.204号#7, 2017.13号#2, 2016.2号#3, 2026.38号#3, 2018.30号#2

**根因**：OCR 服务的 `parsing_res_list` 把图片/盖章标为 `text`（而非 `image`/`figure`）→ `build_page_blocks` 映射为 ParagraphNode。盖章（红色印章）被 OCR 识别了上面的文字 → 当成文本。

**修复**：难在 parser 层修（取决于 OCR 服务 label 准确性）。可加启发式后处理：
- 极短文本（<10 字）+ 位于页面底部（盖章常见位置）→ 标为 image/stamp。
- 或用版面分析标签交叉验证。

**中间过程缺口**：没记录 block_label → IR 映射的决策。
→ **修补**：在 build_page_blocks 里 dump 每个 element 的 label → IR type 映射。

---

### R8：edited-PDF 无编号居中标题未识别（5+ 条）

**涉及**：2025.17号#2, 2024.944号#1, 2024.12.25#1, 2021.行动方案#1, 2022.1086#1

**根因**：`calibrate(use_height_fallback=False)` 对 edited-PDF **不做高度/居中检测**——只做编号正则覆盖。无编号、但字体大/居中的标题（如"广东省自然资源厅关于印发《XXX》的通知"）不被 calibrate 识别为标题。AutoLevel 可能给了它一个 level，但如果 AutoLevel 没识别出来（字号差异不大），就当正文了。

**修复**：在 `calibrate(use_height_fallback=False)` 里也加**高度聚类**（但阈值不同——edited-PDF 的 bbox 高度包含行间距，比值阈值需要调整）+ **居中检测**（x0 居中 → 可能是标题）。

**中间过程缺口**：没记录 AutoLevel 的评分详情（哪些块被判为标题、得分多少）。
→ **修补**：AutoLevel 已有 `heading_level_conf_history`，但 intermediate dump 里可能没有。确认 pipeline debug_dir 的 stage JSON 是否含 conf_history。

---

### R9：LayoutFilter 固定百分比删正文（4+ 条）

**涉及**：2025.17号#3, 2022.129号#2, 2023.89号#2, 2017.13号(隐含)

**根因**：LayoutFilter 的 8% top/bottom strip 固定区域——如果正文延伸到 top 8% 区域（如"第八条"标题靠近顶部），被误删。后处理 `filter_cross_page_noise` 只能移除幸存的重复块，不能恢复被 LayoutFilter 删掉的内容。

**修复**：
- 短期：降低 strip 百分比（8%→5%）。
- 长期：LayoutFilter 改为跨页验证（仅移除多页重复内容）。

---

### R10：MergeStage 合并过激（3+ 条）

**涉及**：2022.129号#1, 2025.17号#1, 2024.2.5#3

**根因**：`_PARAGRAPH_BREAK_SPACING_RATIO` 从 1.8 降到 1.5，但某些文档的段落间距本来就小于行距×1.5（公文行距紧）→ 仍然合并。用户说"上下行间距确实不够宽，也不可能够，因为他们是正文内容"——说明**不能只靠间距判定段落**，还需要其他特征（如首行缩进、编号、内容语义）。

**修复**：加入更多段落断点判据：
- 首行缩进差异（新段落有缩进，续行没有）。
- 编号开头（"1." "2." = 新段落/列表项）。
- 前一行末尾句号（。 → 后面是新段落）。

---

### R11：首页误判表格——HTML 样式 PDF（3 条）

**涉及**：2024.2.5#1, 2022.129号#1(隐含), 2023.89号#1, 2019.2号#1

**根因**：HTML 样式 PDF 的首页布局（红头/标题/发文号）看起来像表格 → pdfplumber/PyMuPDF 检测到表格线 → 误判为表格。另一 session 的 `004-pdf-layout-span-fusion` 部分修了（image_detection 三信号 + table 校验修封面误判）。

**修复**：检查 `004-pdf-layout-span-fusion` 是否已生效（在 main@0e66f41 之后）。如果仍有问题，加更多封面特征检测。

---

### R14：span 排序/同行字体不一信息缺失（3+ 条）

**涉及**：2022.3号#1, 2021.1号#3, 2016.16号#1

**根因**：用户指出"同一行的 span 中，如果有字体不一或加粗的，且有左右相邻的较大间距，可以给同一行的且符合正则表达式的内容加粗处理"——说明 span 级信息（字体/加粗/间距）在中间结果里**不够详细**，无法倒查。

**根因深层**：pipeline 的 element dict 有 `style` 和 `spans`，但 intermediate dump（debug_dir stage JSON）可能只存了 element 级的 `style`，没存每个 span 的详细信息。

**修补中间过程**：在 pipeline 的 debug_dir JSON 里增加 span 级字段（per-span font/size/bold/flags/bbox/origin）。

---

### R15：跨页列表序号错（2+ 条）

**涉及**：2019.5.4#4, 2016.16号#5

**根因**：跨页的列表——page N 末尾 "1.建设用地申请表" + page N+1 开头 "1.用地预审意见"——第二项的序号应该是 "2."，但因为两页独立处理，N+1 页的列表从 "1." 重新计数。

**修复**：跨页列表序号续接——检测上一页末尾的列表序号 → 下一页续接。或在 markdown 重写阶段修正（正则 + 位置推断）。

**中间过程缺口**：没记录跨页列表的序号信息。

---

## 需要修补的中间过程（汇总）

| 缺口 | 涉及根因 | 修补内容 |
|---|---|---|
| join 决策日志 | R1 | `intermediate/join_log.json`：记录哪些块对被检查、join/skip + 原因 |
| bbox 关联状态 | R3, R7 | `intermediate/mapping_log.json`：每个 element 的 label→IR 映射 + bbox 关联成功/失败 |
| 页码检测结果 | R4 | `intermediate/page_number_log.json`：哪些块被判为页码、哪些漏判 |
| AutoLevel 评分详情 | R8 | 确认 debug_dir 的 stage JSON 含 `heading_confidence` + `heading_level_conf_history` |
| span 级信息 | R14 | 在 debug_dir JSON 的 element.spans 里保留 per-span font/size/bold/bbox |
| 列表序号 | R15 | `intermediate/list_order_log.json`：记录列表序号跨页续接情况 |

---

## 优先修复建议

| 优先 | 根因 | 理由 |
|---|---|---|
| 🔴 最高 | **R5 图片 HTML 格式** | 影响所有下游 embedding；检查是否另一 session 改了 export |
| 🔴 最高 | **R2 标题层级决策** | 10+ 条；需用户确认是否改回 1=A（大标题做 H1） |
| 🟡 高 | **R1 跨页 join 条件放宽** | 12+ 条；修条件 + 补 join 日志 |
| 🟡 高 | **R3 viz 少画最上方** | 8 条；查 header 遮挡 + bbox 缺失 |
| 🟡 高 | **R4 页码检测** | 10+ 条；加正则 + 位置检测 |
| 🟡 中 | **R8 edited-PDF 居中/高度标题** | 5+ 条；calibrate 加居中检测 |
| 🟡 中 | **R6 附件拆分** | 6+ 条；新功能 |
| 🔵 低 | R7/R9/R10/R11/R13/R14/R15/R16/R17 | 逐个迭代 |
