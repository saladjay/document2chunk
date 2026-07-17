# issues2 Phase 1 设计规格

> 日期：2026-07-17
> 依据：`openspec/designs/007-issues2-analysis.md`（17 类根因）
> 范围：方案 C 的"现在做"部分（R2 + R5 + R1 + R3 + R6 + R8 + 中间日志）
> 推迟项：R4 页码 pipeline 改、R9 LayoutFilter strip%、R10 MergeStage 多判据、R13 表格模型、R12 OCR 错别字、R14 span 级信息

---

## 1. R2 标题层级——自适应层级推导（C：H1 + metadata）

### 1.1 核心原则

层级由文档**自身特征**推导，不写死 `_STYLE_LEVEL` 绝对值。

三路信号：

| 信号 | 说明 | 适用 |
|---|---|---|
| ① 栈式相对序 | 先遇到的编号样式 = 高层级 | 两路 |
| ② 正文基准反推 | font_size/body（edited）或 bbox_h/body（OCR）比值聚类 | 两路 |
| ③ 目录正推 | TOC 条目层级校准 | 有目录的文档 |

### 1.2 OCR 路线流程

```
1. 收集所有 title 块（跨页），记录 content/bbox_h/编号样式/出现顺序
2. 编号样式做相对序分组（_STYLE_ORDER 偏序 + 栈序覆盖）
3. 大标题（无编号 + ratio > DOC_TITLE_RATIO）：
   - 最长 → HeadingNode(level=1) + metadata.title（保留在 content）
   - 较短 → metadata.custom["doc_titles"]（降级 Paragraph，保留文本）
4. 检测到大标题时：所有编号层级 +1（相对偏移）
5. 无大标题：编号层级不变
6. 无编号非大标题 → 栈序推导（第一次遇到 = 栈顶+1）
7. 栈式单调保证
```

### 1.3 edited-PDF 路线流程

```
1. 保留 AutoLevel 输出（已综合 ② 正文反推 + ③ 目录正推 + bold/缩进/独立行评分）
2. calibrate 增强：
   a. 编号栈序修正：AutoLevel 给的 level 与栈序矛盾 → 以栈序为准
   b. 居中检测：x0 ≈ (page_w - bbox_w)/2 + 无编号 + 字号 > 正文 → 标题
      → 最长居中标题 = H1 + metadata.title（补 AutoLevel 漏检）
   c. calibrate 是增强，不替代 AutoLevel
```

### 1.4 _STYLE_ORDER（偏序，非绝对层级）

```python
_STYLE_ORDER = {
    "chapter": 0, "cn_major": 0,
    "section": 1, "cn_minor": 1,
    "article": 2,
    "digit": 3,
}
# 偏序：值小 ≥ 值大。栈序与偏序冲突时栈序优先。
```

### 1.5 受影响文件

- `src/document2chunk/heading.py`：calibrate 改为自适应推导（偏序 + 栈序 + 大标题 H1 + 居中检测）；_STYLE_LEVEL → _STYLE_ORDER
- `src/document2chunk/ir/models.py`：LogicalDocument 无需改（HeadingNode.level 1-9 已有）
- 两路 extractor 不需改（仍调 calibrate）

### 1.6 测试

- 合成：doc_title + 一、 + （一）→ 期望 doc_title=H1+metadata, 一、=H2, （一）=H3
- 无 doc_title：一、=H1, （一）=H2
- 栈序覆盖偏序：cn_major 先于 chapter 出现 → cn_major ≥ chapter
- edited-PDF 居中标题：合成 element（居中 + 大字号 + 无编号）→ H1

---

## 2. R6 附件拆分为独立 output

### 2.1 架构

```
calibrate → join → filter_noise → split_attachments → assemble(各段)
```

### 2.2 拆分逻辑

`split_attachments(content) -> (main_content, List[attachment_content])`：

1. 遍历 content，检测 `RE_APPENDIX` 命中的 HeadingNode
2. 每个匹配点 = 切分边界：从该 heading 到下一个附表 heading（或文档末尾）= 一个附件段
3. 切分前 = 正文段
4. 附件跨页续接：无新"附表N"heading → 仍属当前附件

### 2.3 IR 扩展（加性）

```python
class LogicalDocument(BaseModel):
    ...
    attachments: List["LogicalDocument"] = Field(default_factory=list)
# model_rebuild() 处理递归引用
```

### 2.4 输出

- batch_test：`output.md` + `output_附件1.md` + `output_附件2.md`（每段独立 assemble + to_markdown）
- `parse()` 返回主 LogicalDocument；`doc.attachments` 是附件列表
- 每个附件独立 heading 层级（calibrate 已 reset prev_level=0）

### 2.5 受影响文件

- `src/document2chunk/ir/models.py`：LogicalDocument 加 `attachments` 字段 + model_rebuild
- `src/document2chunk/heading.py`：新增 `split_attachments(content)` 函数
- `src/document2chunk/extractors/ocr/extractor.py`：调 split_attachments → 多段 assemble
- `src/document2chunk/extractors/pdf.py`：同上
- `scripts/batch_test.py`：写附件为独立 output 文件
- `src/document2chunk/api.py`：parse 返回主 doc + attachments

### 2.6 测试

- 合成：[正文块, 附表1 heading, 附表1 内容, 附表2 heading, 附表2 内容] → 3 段
- 无附件：不拆分（attachments=[]）
- 附表跨页：附表1 内容跨 page 5-6，无"附表2" → 同一附件段

---

## 3. R1 跨页段落 join 放宽

### 3.1 问题

`join_cross_page_paragraphs` 只查紧邻的 `content[i]` + `content[i+1]`，但跨页续接的两段中间可能隔着图片/表格。

### 3.2 修复

改为按页边界查——不要求紧邻：

1. 记录每页最后一个 ParagraphNode 索引 + 每页第一个 ParagraphNode 索引
2. 对相邻页 (N, N+1)，检查 N 的末段 + N+1 的首段
3. `_is_cross_page_continuation` 放宽：去掉 `；：、` 从句号检查（分号/冒号后可继续）
4. 从后往前执行 join（不影响前面的索引）

### 3.3 受影响文件

- `src/document2chunk/heading.py`：重写 `join_cross_page_paragraphs`

### 3.4 测试

- 合成：[P(pg0), Table, P(pg1)] → pg0 末段 + pg1 首段 join（中间隔着 Table）
- 首段以 `：` 结尾 → 不 join
- 首段以 `。` 结尾 → 不 join

---

## 4. R3 可视化少画最上方块

### 4.1 问题

`debug/_annotate.py` 顶部 header 栏（~28px，半透明白色）覆盖页面最上方块的 bbox 框。

### 4.2 修复

1. header 改为**只画底线**（不填充半透明背景）→ 不遮挡 bbox 框
2. bbox 缺失的块记日志（中间过程日志覆盖）

```python
# 旧：overlay_draw.rectangle([0,0,W,header_h], fill=(255,255,255,200))
# 新：overlay_draw.line([(0,header_h),(W,header_h)], fill=(200,200,200), width=1)
```

### 4.3 受影响文件

- `src/document2chunk/debug/_annotate.py`：header 背景改底线

### 4.4 测试

- 合成：doc 第一个块的 bbox 在 y < 28px 区域 → 确认框被绘制（不被遮挡）

---

## 5. 中间过程日志

### 5.1 目标

当块被跳过/join 失败/bbox 缺失/级别被改时，中间结果记录**为什么**。

### 5.2 方案

一个文件 `intermediate/postprocess_log.json`（per-document），记录所有后处理决策。

```jsonc
{
  "calibrate": [
    {"block_id": "...", "text": "...", "detected": "cn_major",
     "action": "→H2", "reason": "doc_title存在→offset+1"}
  ],
  "join": [
    {"page": 0, "last_idx": 5, "next_page": 1, "first_idx": 9,
     "action": "join|skip", "reason": "..."}
  ],
  "bbox_correlation": [
    {"block_id": "...", "status": "missing", "reason": "..."}
  ],
  "attachment_split": [
    {"split_at": "block_000045", "heading": "附表1", "segment": "attachment_1"}
  ],
  "filter_noise": [
    {"block_id": "...", "text": "第1页", "action": "removed", "reason": "..."}
  ]
}
```

### 5.3 实现

各后处理函数（calibrate / join / filter_noise / split_attachments）收集决策列表，extractor 汇总写到 `intermediate/postprocess_log.json`。

### 5.4 受影响文件

- `src/document2chunk/heading.py`：各函数增加可选 `_log` 参数（list），追加决策记录
- `src/document2chunk/extractors/ocr/extractor.py`：传 `_log` 列表 → 写文件
- `src/document2chunk/extractors/pdf.py`：同上

---

## 6. R5 图片 markdown 格式

**状态**：已确认当前代码输出 GFM `![alt](image_id)`（非 HTML）。如 issues2 报告 HTML 则是旧输出残留，重跑即修正。**无需改动**。

---

## 7. R8 edited-PDF 居中标题

**已在 §1.3 覆盖**：calibrate 增加居中检测（x0 居中 + 无编号 + 字号 > 正文 → H1）。不单独列实现。

---

## 8. 不在本次范围（推迟）

| 根因 | 推迟原因 |
|---|---|
| R4 页码过滤 | 需改 pipeline PageNumberDetection / 加 OCR 路线正则 |
| R9 LayoutFilter strip% | 需改 pipeline stage（高风险） |
| R10 段落多判据 | 需改 MergeStage（加首行缩进/编号/句号判据） |
| R13 表格模型 | 需集成专用表格 API |
| R12 OCR 错别字 | 服务限制 |
| R14 span 级信息 | 需改 pipeline debug dump 格式 |
| R15 列表序号续接 | 需跨页列表状态 |
| R7 盖章→text | 需启发式后处理 |
| R11 首页误判表格 | 已由 004-pdf-layout-span-fusion 部分修 |
| R16 公式误识别 | OCR 服务限制 |
| R17 重复字符 | OCR 服务限制 |

---

## 9. 执行顺序

1. **R2 calibrate 自适应层级**（影响最大：10+ docs）+ R8 居中检测
2. **R1 join 放宽**（12+ docs）
3. **R6 附件拆分**（6+ docs）
4. **R3 viz header**（8 docs）
5. **中间过程日志**（诊断增强）
6. 全套测试 + 全量 23 PDF 重跑验证
