# INTEGRATION.md — 会话间握手契约

> 这是并行 Claude 会话之间的"API"。各会话**按此编码**；若需改接口，必须先在 `SESSIONS.md` 登记。
> 权威契约 = `ir-model`（`src/document2chunk/ir/`，已冻结）+ 本文件。

## 1. 模块依赖与数据流

```
source ──▶ extractor.extract() ──▶ ExtractionResult(content, metadata, toc_entries)
                                         │
                 api/orchestrator 调用    ▼
                            structure.assemble(result, keep_toc) ──▶ LogicalDocument
                                         │
                            ┌────────────┼────────────┐
                            ▼            ▼            ▼
                         export      debug/viz     下游 RAG
```

- extractor **不**依赖 structure-builder（二者只依赖 ir-model）→ 可完全并行。
- `api` 是唯一接线点（调用 extractor → assemble）。

## 2. extractor 接口（所有 extractor 统一）

```python
class Extractor(Protocol):
    source_type: SourceType
    def extract(
        self,
        source: str | Path | bytes,
        *,
        options: ParseOptions | None = None,
    ) -> ExtractionResult: ...
```

- **必须**：返回 `ExtractionResult`；`content` 中 `HeadingNode.level` 已判定（1–9）；`metadata.source_type` 已设。
- **必须**：PDF/OCR 节点带 `provenance`（page_index/bbox）；docx 节点 `provenance=None`。
- **禁止**：extractor 内部调用 structure-builder 或产出完整 `LogicalDocument`。

## 3. structure.assemble（structure-builder 提供）

```python
def assemble(
    result: ExtractionResult,
    *,
    keep_toc: bool = False,
) -> LogicalDocument: ...
```

- 单遍栈算法建 `section_tree` + `block_to_section`；可选 `toc_entries` 校准 level / 产出 `TocNode`。
- 返回完整 `LogicalDocument`（`section_tree` 非空）。

## 4. pipeline `debug_dir` JSON schema（session ① 写、session ③ debug 读）

`Pipeline(debug_dir=...)` 每 Stage 后写 `{NN}_{name}.json`：

```json
{"stage_index": int, "stage_name": str, "stage_type": "global"|"local",
 "pages": [{"page_index": int, "elements": [<pipeline element dict>, ...]}]}
```

- element dict schema 见 `designs/003` §6（`type/label/level/text/markdown/bbox/order_index/style/spans/heading_confidence/heading_level_conf_history`）。
- `debug_dir=None` 时零开销。

## 5. export 入口（session ② 提供）

```python
def to_json(doc, *, pretty=True) -> str          # 规范输出
def to_markdown(doc, *, include_metadata=False) -> str
def to_plain_text(doc) -> str
def to_jsonl(doc) -> str                          # 兼容旧接口
```

## 6. api（session ③ 提供，最后集成）

```python
def parse(source, *, source_type=None, keep_toc=False, extract_images=True, options=None) -> LogicalDocument
# 路由: .pdf editable→pdf, scanned/mixed→ocr, .docx→docx, 图片→ocr
# 流程: extractor.extract → structure.assemble → LogicalDocument
```
HTTP：`POST /parse`（multipart file）→ `{document, markdown}`。

## 7. 接口变更规则

- 改本文件任何接口 → **必须**在 `SESSIONS.md §接口变更日志` 登记，并 @受影响 session。
- `ir-model` **冻结**：需新节点/字段 → 走协调人（统一加性扩展 + 更新 spec + 冒烟测试），各 session 不得私改。
