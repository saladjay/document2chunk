# Python 编码规范 — document2chunk

> 所有模块（含 Qoder 的 `pdf-extractor`）必须遵守。语言无关的约定继承自 `doc-paddle-ocr/refraction2/RULES.md`，已剥离 Java/MSDP 内容。

## 1. 项目布局

```
document2chunk/
├── src/document2chunk/        # 源码（src-layout）
│   ├── ir/                    # ★ 规范 IR 契约（零业务依赖，最先冻结）
│   ├── extractors/            # 各格式 extractor（pdf/docx/ocr/...）
│   ├── pipeline/              # span 处理 Stage 引擎（pdf/ocr 内部依赖）
│   ├── structure/             # 章节树构建（structure-builder）
│   ├── export/                # 导出器
│   └── api.py                 # parse() 入口 + FastAPI
├── tests/                     # pytest
└── openspec/                  # SDD 文档
```

- Python **3.10+**。所有公共 API 必须带类型注解。
- `src-layout`：`pyproject.toml` 配置 `[tool.hatch.build.targets.wheel] packages = ["src/document2chunk"]`，`[tool.pytest.ini_options] pythonpath = ["src"]`。

## 2. 依赖方向（铁律）

```
api → extractors → ir-model
                 ↘ pipeline ↗
structure-builder → ir-model
export → ir-model
```

- **extractor 之间禁止横向依赖**。公共 span 处理逻辑放 `pipeline/`，由 pdf/ocr extractor 各自引用。
- **`ir-model` 是零业务依赖的叶子**（仅 pydantic），任何 extractor/模块只**导入**它，不得反向依赖。
- **禁止**：`ir-model` import 任何 extractor / pipeline / 第三方解析库（PyMuPDF/lxml/PaddleOCR）。

## 3. 命名约定

| 类别 | 约定 | 示例 |
|---|---|---|
| Stage 类 | `XxxStage` | `BodyAnalysisStage`、`AutoLevelStage` |
| Extractor/Parser 类 | `XxxExtractor` / `XxxParser` | `PdfExtractor`、`DocxExtractor` |
| 模块级常量 | `UPPER_SNAKE` | `IMAGE_MIN_AREA`、`LAYOUT_DPI` |
| 内部常量/函数 | `_` 前缀 | `_DOT_LEADER_RE`、`_bbox_overlap()` |
| JSON / IR 字段 | `snake_case` | `page_index`、`block_to_section` |
| 异常类 | `XxxError`，继承模块基类 | `ParseError(Document2ChunkError)` |

## 4. pydantic v2 用法

- IR 节点用 **pydantic v2 BaseModel**；判别联合用 `Annotated[Union[...], Field(discriminator="type")]`。
- 自引用/递归模型（`SectionNode.subsections`、表格/列表嵌套块）须在模块末尾调用 `model_rebuild()`。
- 序列化规范输出：`model_dump_json(exclude_none=True)`；反序列化：`model_validate_json(...)`。
- **禁止**用 v1 风格 `class Config:`，改用 `model_config = ConfigDict(...)`。
- 字段缺省值用 `Field(default_factory=...)`，**禁止**可变默认值直接赋值。

## 5. ID 与顺序

- 节点 ID 单文档内唯一稳定，格式：`block_000001` / `sec_000001` / `run_000001`（6 位补零，1-based）。
- `content` 必须保持源阅读顺序：PDF/OCR 按 `(page_index, y_top, x0)`；docx 按 `<w:body>` 顺序。

## 6. provenance 约定

- PDF/OCR 节点：`provenance=Provenance(source_type=..., page_index=..., bbox=[...])`；OCR 加 `confidence`。
- docx 节点：`provenance=None`（**禁止**给 docx 节点塞 bbox/page_index）。

## 7. 异常与降级

- 每个顶层模块定义自己的异常基类（继承 `document2chunk.Document2ChunkError`），层次清晰。
- **局部恢复优先**：单个段落/表格/页解析失败 → 记录 WARN + 跳过 + 继续，不中断整体。
- **Fast Fail**：关键文件缺失 / 文件损坏 → 立即抛明确异常（如 `InvalidSourceError`）。
- 可选依赖缺失：`try: import xxx except ImportError: ...` 并在文档标注 optional-extra。

## 8. 日志

- 标准库 `logging`；结构化（含模块名、级别、上下文）。
- WARN：样式继承链断裂、未知节点、启发式命中。
- ERROR：关键文件缺失、解析异常。
- **禁止**在日志中记录文档正文敏感内容（与 SEC 需求一致）。

## 9. 测试

- `pytest` + `pytest-cov`。
- 覆盖率目标：`ir-model`、`structure-builder` ≥ 90%；extractor ≥ 80%；整体 ≥ 80%。
- 每个契约模块配冒烟测试（见 `tests/test_ir_smoke.py` 范式：构造 → 序列化往返 → 查询）。
- extractor 测试须有 fixtures 文档样本 + 预期输出。

## 10. 提交与反模式（禁止）

- ❌ 三层冗余（Factory+Manager+Handler）。
- ❌ 「仅被调用一次」的包装函数。
- ❌ 内嵌在循环里的闭包函数（提到模块级）。
- ❌ 硬编码魔法数字（集中到 `config`/常量）。
- ❌ 双重导入兼容（`try: import fitz except: import pymupdf`）——依赖已声明就直连。
- ❌ 微服务化（包能共存则单体库 + 可选 HTTP）。
