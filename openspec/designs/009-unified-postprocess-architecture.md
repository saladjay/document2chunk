# designs/009 —— 统一 BlockNode 全文档后处理架构

> 取代 designs/006 的「逐页 9-stage SplitPipeline + 后处理四函数」双系统架构。
> 解决 issues1/2/3 反复打补丁的根因：**决策点重复 + 逐页丢跨页上下文 + 固定阈值**。

## Context（为什么）

issues1/2/3 循环的根因不是单条规则写错，而是**同一关注点有多个决策点互相覆盖**：

| 关注点 | 旧决策点（重复） | 后果 |
|---|---|---|
| 标题类型 | ClassificationStage ＋ calibrate doc_title fallback | 打架 |
| 标题层级 | AutoLevel ＋ TOCAnalysis ＋ calibrate | 三方覆盖 |
| 跨页合并 | MergeStage(逐页) ＋ join_cross_page_paragraphs | 两套规则 → R1 |
| 噪声过滤 | LayoutFilter(逐页 8% strip) ＋ filter_cross_page_noise | 两套规则 → R9 过度过滤 |
| 页码 | PageNumberDetection(正则+70%) | 漏 `1,2,3`/`321/322` → R4 |

## 目标架构

```
PDF:  PyMuPDF → 线性 Pipeline [BodyAnalysis, ImageDetection, Classification, TOCDetection, MergeStage]
                → elements_to_blocks → (blocks, toc_entries)
OCR:  服务 → markdown → build_page_blocks → blocks → (blocks, toc_entries=None)
两路 → postprocess(blocks, metadata, *, toc_entries, page_geometry, layout_data):
        1. filter_noise       跨页页眉/页脚/页码（layout 证据 + 跨页重复 + 页码序列；绝不盲删顶/底 N%）
        2. merge_cross_page   跨页段落续接 + 多行标题合并
        3. calibrate_levels   栈式定级 + doc_title→H1 + appendix reset + toc 覆盖 + R2 提升
        4. split_attachments  附件边界拆分
      → (main_content, attachments) → assemble → LogicalDocument
```

**单一决策点**：类型=Classification(PDF)/markdown `#`(OCR)；层级=calibrate_levels；
合并=MergeStage(页内)+merge_cross_page(跨页)；噪声/页码=filter_noise；附件=split_attachments。

### 为什么 OCR 不并入 element-dict pipeline
OCR 服务产 BlockNode 直连，**缺 span 级 font/size 信号**——Classification 的字号比值信号对
OCR 无效。强行并入会降级且需 1-2 周。故 OCR 保持直接产 BlockNode，与 PDF 在 BlockNode 层汇合，
共用 postprocess。OCR 的标题类型由 markdown `#` 决定（build_page_blocks 内）。

## 关键设计点

### filter_noise 三证据分层（修 R4 + R9）
绝不盲删顶/底 N%（R9 根因），必须命中三类证据之一：
1. **layout 强证据**：版面框标了 header/footer/number → 中心点落入（±5% 扩展）即移除。
2. **跨页重复**：顶/底 8% 带内文本（数字归一化为 `#N`，兜住"第 1 页"/"第 2 页"）在 ≥3 页
   同一位置出现且文本 ≥10 字符 → 移除（防误删跨页章节标题）。
3. **页码序列**：底部 70%-100% + 同行较窄（< 同行最大宽 ×50%）+ 纯数字/`N/M`/`第N页`，
   形成跨页递增序列（≥3 命中，容忍首页缺失）→ 移除。抓 `1,2,3,4` + `321/322`。

### calibrate_levels 的 R2 提升（唯一类型变更例外）
OCR 服务常把文档大标题标成 `text` → ParagraphNode，旧 calibrate 只扫 HeadingNode → 漏检（R2 根因）。
calibrate_levels 先 pre-scan：所有 BlockNode（含 ParagraphNode）算 `ratio=bbox_h/body_h`，无编号 +
ratio≥1.8（OCR）或居中+ratio≥1.2（edited）→ 转 HeadingNode(level=1)。这不是重复决策——OCR 无上游
Classification，此为唯一入口；对 PDF 是漏检安全网。

### toc_entries 回写（消除 assemble 双重消费）
`structure/builder.assemble` 也消费 toc_entries 校准 level。calibrate_levels 完成后回写
`TocEntry.level`，使 assemble 的消费变 no-op（等价），向后兼容。

### SplitPipeline → 线性 Pipeline
瘦身后 PDF 只需 5 stage，**不再需要 toc/content 分相编排**——toc_entry 不进 content、MergeStage
只合并 paragraph/heading（toc_entry 类型不在合并白名单，已验证安全）。LayoutFilter/TOCAnalysis/
AutoLevel/PageNumber 的职责全部上移到 postprocess。

## 消除的重复（robustness 收益）

| 移除/折叠 | 取代为 | 修的 issue |
|---|---|---|
| calibrate doc_title 类型提升副作用 | calibrate_levels 单点 | 双标题系统 |
| join_cross_page_paragraphs | merge_cross_page（全文档） | R1 跨页 |
| filter_cross_page_noise + LayoutFilter strip% + PageNumberDetection 正则 | filter_noise（跨页证据） | R9 + R4 |
| AutoLevel 段落提升 | Classification + calibrate_levels | 双层级系统 |
| SplitPipeline 6 相编排 | 线性 Pipeline | 复杂度 |
| _classify_ocr 死代码 | （OCR 不走 pipeline） | — |

## 验证

- 单测：`tests/test_postprocess.py`（17→18 用例：filter_noise 三证据 / merge_cross_page /
  calibrate_levels+toc+R2 / split_attachments / postprocess 集成）。
- 引擎：`tests/test_pipeline.py` 改为线性 `pdf_pipeline` 测试。
- 全套 `pytest`：**100 passed**。
- 真实 PDF 冒烟：test2.pdf（82 块、跨页 join、doc_title 检测生效）；4 份政策 PDF 批量无崩溃。

## 后续（未完成）

- issues3 全语料（25 PDF）回归 diff：需源 PDF 路径（用户提供），重构前后各跑 `batch_test.py`
  对比 `document.json`。OCR 路径回归需 OCR token。
- designs/008 的 R4/R9/R2 状态待全语料验证后标"已修"。
