# Session ② 任务书 — DOCX/结构/输出族

> 你是一个并行 Claude 会话。本文件自包含。
> 分支：`feat/docx-structure-export`。状态：待开。

## 你的范围

- `structure-builder`（章节树构建 + **`assemble()` 握手函数**）
- `docx-extractor`（lxml 直读 OpenXML → IR）
- `export`（Markdown / JSON(AST) / PlainText / JSONL）

## 开工前必读（按顺序）

1. `openspec/project.md`
2. `openspec/designs/001-target-architecture.md`（IR 定义 §4、章节树）
3. `openspec/INTEGRATION.md`（你的接口契约 §3 §5）
4. `openspec/SESSIONS.md`
5. `openspec/specs/structure-builder/spec.md`、`specs/docx-extractor/spec.md`、`specs/export/spec.md`
6. `src/document2chunk/ir/`（**只读 import**）
7. `docs/coding-standards.md`
8. （docx 参考）`D:\github\doc-paddle-ocr\document-to-chunk\{SDD,SRS,technical-spec}-Word.md`（lxml/AST 原设计，思路参考，不要照抄其 python-docx 相关内容）

## 接口契约（必须遵守）

```python
# structure-builder 提供给 api 调用（INTEGRATION §3）
def assemble(result: ExtractionResult, *, keep_toc: bool = False) -> LogicalDocument

# extractor（INTEGRATION §2）
def extract(source, *, options=None) -> ExtractionResult   # docx: provenance 全 None

# export（INTEGRATION §5）
def to_json(doc, *, pretty=True) -> str
def to_markdown(doc, *, include_metadata=False) -> str
def to_plain_text(doc) -> str
def to_jsonl(doc) -> str
```

## 任务（对齐 `openspec/tasks.md` §3 §4 §6）

### structure-builder（最先做，别人依赖 assemble）
1. 栈算法 `assemble`：`ExtractionResult.content`（HeadingNode.level 已判）→ `section_tree`(嵌套) + `block_to_section`。
2. `toc_entries` 校准 level（信号消费）+ `keep_toc=True` 产 `TocNode`。
3. 边界：无标题归 root、层级跳跃、level>9 截断。
4. **assemble 要能消费任意 extractor 的 content**（PDF 的也行）——这是解耦的关键。

### docx-extractor
1. `PackageReader`（zipfile + lxml `recover=True`）。
2. `StyleRegistry`（basedOn 继承链 + 缓存 + 循环检测）。
3. `DocumentParser`：`<w:p>`→Paragraph/Heading、`<w:tbl>`→TableNode(gridSpan/vMerge)、列表→ListNode、`<w:drawing>`→ImageNode、`<w:hyperlink>`→HyperlinkNode、`<w:r>`→RunNode。
4. 标题检测优先级：`outlineLvl` > pStyle 继承链 > 启发式（可配置）。
5. TOC 域识别（`fldSimple`/SDT/instrText "TOC"）→ 走 `toc_entries` 信号。
6. **provenance 全 None**（docx 不算 bbox/页眉/页码）。

### export
1. `to_json`（规范，`model_dump_json(exclude_none=True)`，可往返）。
2. `to_markdown`（遍历 section_tree，Heading N→`#`×N，表格管道格式，列表/图片）。
3. `to_plain_text`、`to_jsonl`（兼容）。

## 验收

- docx 样本 → `LogicalDocument`（`source_type=docx`，provenance 全 None，标题层级对）。
- `assemble` 对一份**PDF content**（mock）也能正确建树。
- `to_json` → `model_validate_json` 往返一致；`to_markdown` 层级正确。
- ir-model 冒烟测试仍绿。

## 协作注意

- **你提供 `assemble` + `export`** 给 Session ③ 的 api 调用——接口按 INTEGRATION，勿擅改；要改在 `SESSIONS.md §5` 登记。
- **禁止改 `ir-model` / `pipeline/`**。
- docx 是全新代码（lxml），不碰 `doc-paddle-ocr` 的 PDF 源码。
- `extract()` 返回 `ExtractionResult`（非完整 LogicalDocument），由 api 调 assemble。
