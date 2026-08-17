# 阶段 A 设计：DOCX 接入统一后处理

> 日期：2026-08-17
> 状态：已获用户批准
> 分支：`feat/docx-postprocess`（新 worktree）
> 姊妹文档：`2026-08-17-docx-ooxml-coverage-phase-b-handoff.md`（阶段 B 交接，另一会话执行）

## 1. 目标与范围

### 1.1 背景

`document2chunk` 的 PDF 与 OCR 两路引擎都在 extractor 内部调用统一后处理
`postprocess()`（filter_noise → merge_cross_page → calibrate_levels →
split_attachments → merge_split_tables，见
`openspec/designs/009-unified-postprocess-architecture.md`）。DOCX 路目前从
`DocumentParser.parse()` 直接构造 `ExtractionResult`，完全绕过后处理，因此缺：

- **calibrate_levels**：栈式自适应标题定级、doc_title 检测、toc_entries
  覆盖、附件边界重置。issues5 中「多级标题识别不准」「`一、` 应为三级」
  即此类问题。
- **split_attachments**：附件/附录边界拆分。issues5 中「附件当作单个文件
  处理」「附件分割程序有误」即此类问题。
- **merge_split_tables**：同表头连续表格合并。

### 1.2 目标

把 DOCX 变成第三路汇入统一 postprocess 的引擎路径，对齐 PDF/OCR 的输出
质量，用真实公文 docx 验收。

### 1.3 非目标（→ 阶段 B）

header/footer part、脚注/尾注、文本框内容、OMML 公式、嵌入对象等
OOXML 覆盖面扩展。见姊妹交接文档。

## 2. 架构与数据流

```
                        ┌─ PdfExtractor ──┐ (page_geometry, layout_data, use_height_fallback=False)
源路由 → extractor ──→ ┤─ OcrExtractor ──┤ → postprocess() → (main, attach_segs) → ExtractionResult
                        └─ DocxExtractor ─┘ (page_geometry=None, use_height_fallback=False)  ← 新增
```

DOCX 在 `parser.parse()` 产出 blocks 后、构造 `ExtractionResult` 前，调用：

```python
main_content, attach_segments = postprocess(
    blocks, metadata,
    toc_entries=toc_entries,
    page_geometry=None,
    use_height_fallback=False,
    _log=pp_log,
)
```

`attach_segments` 按 `extractors/pdf.py:718-721` 的既有模式追加：

```python
result = ExtractionResult(content=main_content, metadata=metadata, toc_entries=toc_entries or None)
for seg in attach_segments:
    result.attachments.append(ExtractionResult(
        content=seg,
        metadata=<docx metadata 副本，custom={"is_attachment": True}>,
    ))
```

`api.py` / `serve.py` 零改动——postprocess 在 extractor 内完成，与
PDF/OCR 架构对称。

## 3. 组件改动

| 文件 | 改动 |
|---|---|
| `extractors/docx/extractor.py` | `extract()` 末尾接 `postprocess()` + attachments 组装（含 debug 目录落 `postprocess_log.json`，同 pdf.py 模式）；计算 DOCX 正文基准字号：全部 run 的 `font_size` 众数（仿 `BodyAnalysisStage` 的众数基准思路），供 doc_title 字号比用 |
| `extractors/docx/parser.py` | ① heading 来源标记：`heading_source = "style" \| "heuristic"` 写入 HeadingNode 的 metadata dict（outlineLvl/pStyle → style；正则 → heuristic）；② 伪标题预扫描：无样式短编号段落提升为 HeadingNode（复用 `postprocess.style_of()` 的编号正则；条件：无 pStyle heading/outlineLvl + 文本长度上限 + 不以句尾标点结尾），level 先置临时值 2 占位（`HeadingNode.level` 必填 int≥1；提升条件含编号正则命中，calibrate 编号栈必然覆盖占位值）；③ 段落 `pPr/jc=center` 写入 `metadata["centered"]=True`（doc_title 检测用） |
| `postprocess.py` | ① `calibrate_levels` 主循环加 style-authoritative 分支：`heading_source == "style"` 的 HeadingNode 层级保留（仅加 doc_title 的 `level_offset`），插入优先级序列 toc 覆盖之后、编号栈之前；② `_promote_doc_title_paragraphs` 加 DOCX 变体：候选 = `metadata["centered"]` 或大字号段落，提升门槛对齐 edited-PDF 双条件——centered 且 `run font_size / body_font_size ≥ 1.0`，或无 centered 标记时 `≥ 1.2`（DOCX run 自带磅值，无需 bbox）；无候选时沿用现有 fallback（首个无编号 L1/L2 且 ≥8 字）；③ DOCX 正文基准字号经新参数传入（如 `body_font_size: Optional[float]`） |
| `postprocess.py`（防御） | `filter_noise` / `merge_cross_page` 在 blocks 无 provenance、page_geometry=None 时的 None 安全（预期现有代码已安全，用测试锁定行为） |

## 4. 关键决策与理由

1. **样式层级权威，栈序只救场**。outlineLvl/pStyle 是作者意图，比编号
   栈可靠；issues5 的「`一、` 应为三级」问题出在*无样式*伪标题上，栈式
   定级正好接手。最终优先级：toc 覆盖 > 样式 level > 编号栈 > 保留原
   level。
2. **doc_title 用字号比而非 bbox 高度比**。DOCX 无页几何，但 run 自带
   真实字号（磅）；公文标题（二号 ≈22pt）vs 正文（三号 ≈16pt）比值
   ≈1.33 ≥ 1.2，信号直接可用。
3. **`use_height_fallback=False`**。与 edited-PDF 同路。README 记载的
   教训：OCR 高度比提升在已有可靠标题信号时会误提升正文段落，DOCX 有
   样式信号，不走该路径。
4. **伪标题提升放在 parser 而非 postprocess**。判断「无样式」需要
   pStyle/outlineLvl 信息，这是 DOCX extractor 的私有上下文；postprocess
   只消费带 `heading_source` 标记的统一 BlockNode。
5. **merge_cross_page / filter_noise 对 DOCX 基本无操作**。DOCX 无页
   概念（provenance 全 None、page_geometry=None），跨页续接与页眉页脚
   过滤天然不触发；DOCX 的页眉页脚在独立 XML part 中，本就不进
   document.xml body。调用它们只为统一管线顺序，不追求效果。

## 5. 错误处理

- postprocess 内部维持现状：不新增异常类型。
- DOCX 路唯一新失败模式是 postprocess 与 parser 的标记契约不匹配
  （如 `heading_source` 缺失），按 `ExtractionError` 包装向上抛。
- debug 模式下 `_log` 落 `<debug_dir>/postprocess_log.json`（同 pdf.py），
  供真实样本排查；不静默吞块。

## 6. 测试与验收

### 6.1 单测

扩展 `tests/test_docx.py` 与 `tests/test_postprocess.py`：

1. 无 provenance 的 filter_noise / merge_cross_page：不崩、不改块。
2. 样式 heading（outlineLvl/pStyle）层级不被栈改写（仅 doc_title
   offset 生效）。
3. 无样式编号段落 → 伪标题 → 栈定级：构造「二级样式标题下的一、/二、
   编号段落」，断言落 H3（复现 issues5 场景）。
4. doc_title 字号比提升 + `metadata.title` 回写 + 竞争候选降级
   Paragraph。
5. 附件拆分：`result.attachments` 非空、`custom={"is_attachment": True}`。
6. 同表头连续表格合并。
7. 回归：PDF/OCR 全量测试不变绿不合并（postprocess.py 为共享代码）。

### 6.2 验收

- 构造样本先行开发（tests/fixtures 新增 docx 构造器或手工文件）。
- 真实公文 docx 目录由用户在验收阶段提供（本地目录，路径届时给出）——
  **外部依赖**，不阻塞开发，阻塞验收。
- 逐份对比标题树 + 附件数，人工抽查正文归属。

## 7. 阶段 B 交接（本 spec 附带交付）

`docs/superpowers/specs/2026-08-17-docx-ooxml-coverage-phase-b-handoff.md`：
自包含交接文档，覆盖 OOXML 覆盖面补齐的范围、仓库约定、代码指针、验收
标准。B 在 A 合入 main 后开工。

## 8. Worktree 策略

- 新开 `feat/docx-postprocess` worktree（superpowers 工作流建立）。
- 陈旧 worktree `document2chunk-docx`（`feat/docx-structure-export` 已
  全部合入 main）不动、不清理——本任务不触碰。
