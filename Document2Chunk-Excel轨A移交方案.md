# Document2Chunk — Excel 轨 A（chunk2embedding）移交方案

> 移交日期：2026-09-20。移交对象：Qoder（AI 编码代理）。
> **本文档是移交的唯一入口。** Qoder 操作者按 §0 的顺序加载材料后即可开工。
> 设计已冻结（经 19 轮 grill 评审 + 真实语料盘点定档）——**Qoder 的职责是忠实执行，不是重新设计**。

---

## 0. 加载顺序（操作者执行清单）

| 步骤 | 动作 | 材料 |
|---|---|---|
| 1 | 通读本文档 | 本文件 |
| 2 | 按 §2 的清单读上下文 | 4 份规格文档（§2.3） |
| 3 | 把 §3 编程重点 + §5 DoD 配置为 Qoder 的规则/系统提示（Rules） | 本文件 §3/§5 |
| 4 | 按**实施计划逐任务执行**（唯一编码依据，代码已全部给出） | `docs/superpowers/plans/2026-09-20-excel-track-a-chunk2embedding.md` |
| 5 | 每任务结束跑计划内的验证命令；全部完成后按 §5.4 验收 | — |

**禁止事项**：修改已定稿的 schema/键规则/值规则；引入计划外依赖；跳过失败测试直接写实现；把 §8"明确不做"的内容做进来。

---

## 1. 任务一句话

为 document2chunk（Python 文档解析服务）新增 Excel 解析：xlsx/xlsm/xls/et/csv → **行级 JSON**（每行 `{data, text, meta}`，键为表头路径如 `华东-子列1`），暴露 `POST /parse-excel`，供下游 RAG 分块与检索。这是双轨架构中的**轨 A**；轨 B（text2sql 数据准备）是二期，**本次不做**。

## 2. 上下文信息

### 2.1 项目背景

- 仓库 `D:\github\document2chunk`，src 布局，主包 `src/document2chunk/`；FastAPI 服务（Docker 对外 9301），另有 CLI（`serve.py`）。
- 已有 docx/PDF 解析管线；核心依赖仅 `pydantic`，其余走 extras（`pdf/ocr/docx/legacy/api/viz/dev`）。
- 老格式归一化链路已上线：`format_detect.py` 魔数指纹（已支持 XLSX/XLS 识别）+ `legacy_convert.py` soffice 转换（独立 profile、超时杀进程树）。
- 当前对表格文件的行为是 `serve.py` 直接 400"即将支持"——**本次要替换这个行为**。

### 2.2 代码库关键挂点（探索已核实，含 file:line）

| 挂点 | 位置 | 用途 |
|---|---|---|
| 端点定义区 | `src/document2chunk/api.py`（`create_app()` 闭包内；`/parse-doc` 在 553-556） | 新增 `/parse-excel` 的落点，照 `_chai_parse`（413-519）的并发/线程池模式 |
| 错误→HTTP 映射 | `api.py:396-407` | `UnsupportedFormatError`→400、`Document2ChunkError`→422（新 `ExcelParseError` 自动 422） |
| 并发信号量 | `api.py:36-39` + `run_in_threadpool`（69-86） | `_PARSE_CONCURRENCY`（env 默认 2） |
| 表格 400 拦截 | `serve.py:167`（`_SPREADSHEET_EXTS`）、`185-189`（`_xls_err`）、`206-207` | 文案改为指向 `/parse-excel`；`tests/test_serve_legacy.py:100-108` 断言同步改 |
| soffice 间接层 | `serve.py:174-177`（`_legacy_convert`，供测试 mock） | 老格式/假扩展名转换走它 |
| 指纹识别 | `format_detect.py`：`identify(data)->Detection`（147-156）、`FileKind.XLSX/XLS`（21-37）、zip `xl/` 前缀（86）、OLE2 `Workbook`（51） | **已完备，直接用**；若 `Detection` 字段名与计划假设不同，以实际为准 |
| SourceType 枚举 | `ir/enums.py:14`（`XLSX` 已预留） | 无需新增 |
| 无缓存公式探测 | 计划 Task 2 的 openpyxl 双 load | `data_only=True` 读不到公式类型标记，必须 `data_only=False` 再扫一遍 |
| 日志/计时 | `timing.py`（`log_arrive/StageTimer/finish`）、api.py:445-448 调用形 | 端点观测照 `_chai_parse` 补齐 |
| Docker extras | `Dockerfile.d2c` 末段 `pip install ".[...]"` | 追加 `excel` |
| 无锁文件卫兵 | `~$` 前缀（165 B Excel 锁文件） | 入口拒绝（校验在验收阶段补一条守卫测试即可） |

### 2.3 必读文档（按序，全部在仓库根目录/ plans 目录）

| # | 文档 | 读什么 | 为什么 |
|---|---|---|---|
| 1 | `docs/superpowers/plans/2026-09-20-excel-track-a-chunk2embedding.md` | **全文，逐字**。11 个任务的 Files/Interfaces/完整代码/验证命令/commit 消息 | 唯一编码依据 |
| 2 | `Document2Chunk-Excel解析技术调研报告.md` | §2 双轨架构与分歧表、§3 管道、§3.6 路线分流、§4 schema 与已定规则 | 理解"为什么这么设计" |
| 3 | `Document2Chunk-Excel难点定档表.md` | §1 定档总表、§2 验收标准、§3 合成用例 | 知道每个代码块对应哪个难点、P0 边界在哪 |
| 4 | `Excel语料盘点-draft.md` | §1.3 频次、§1.4 假扩展名、§1.5 启发式误报、§2 P0 清单 | 验收语料与真实样本行为 |

### 2.4 已定稿的关键决策（改变即返工，出处见调研报告 §4.2）

- **键**：表头路径 `-` 连接（`华东-子列1`）；表头内空白压单空格；撞名`（2）`；空表头`列N`。
- **值**：显示值规范化——千分位不产出、货币符保留（`¥1024.50`）、百分比保留 `%`（`12%`）、日期 ISO、字符串 trim、空值→None。
- **分层**：溯源/flag 只进 `meta`，不进 `data`、不进 `text`；`text` = `"键: 值; "` 平铺，null 不进。
- **合并单元格**：值下放每一行，不加标记。
- **合计行**：轨 A 保留 + `meta.from_total` 打标。
- **隐藏**：隐藏 sheet 跳过（warning）；隐藏行/列解析 + sheet 级打标。
- **envelope**：`{file, warnings, rows[], blocks[], sheets[]}`。

## 3. 编程重点（10 条硬纪律，配置为 Qoder 规则）

1. **TDD 铁律**：每个任务先写失败测试并运行确认失败（`test_red_*` 前缀，仓库惯例），再写实现，再确认通过，再 commit。禁止颠倒。
2. **坐标纪律**：代码内部全 0-based 闭区间；输出 `row` 字段用 xlsx 1-based 真实行号。混淆=验收必挂。
3. **不信声明 dimension**：以 calamine `skip_empty_area=True` 的实际扫描值域为准（真实语料里"声明小于实际"有 2 例，"声明虚胖"0 例——方向和直觉相反）。
4. **openpyxl 双 load**：`data_only=True` 读值/格式，`data_only=False` 扫公式坐标；无缓存公式 = 公式坐标上 data_only 值为空。单 load 探测不到。
5. **防吞数据区**：顶部连续全字符串行数作表头时，若下方无类型对比（全表皆字符串），表头封顶 1 行，交路线分流判断——纯文本 FAQ sheet 曾实测把 30 行全吞成表头。
6. **词表只匹配行首列**：合计行识别的第一信号仅看该行第一非空单元格，防止"合计金额"列名误杀数据行。
7. **毒输入兜底**：`parse_excel_bytes` 顶层 catch → `ExcelParseError`（422）。任何毒文件都不允许 500（MuPDF 毒 PDF 事故的同款教训）。
8. **性能护栏**：openpyxl 二遍读取设 `_OPENPYXL_CELL_LIMIT = 200_000`，超限跳过并 warning；读取主力永远是 calamine。
9. **依赖纪律**：新依赖只进 `excel` extra（`python-calamine>=0.7.0`、`openpyxl>=3.1.0`）；核心依赖保持仅 `pydantic`；**禁止引入 pandas**。
10. **commit 纪律**：`type(scope): 中文描述——说明` + 结尾 `Co-Authored-By: Claude <noreply@anthropic.com>`；每个任务一个 commit（消息在计划里已写好）。

## 4. 需要加载的 Skill（Qoder 侧）

| 能力 | 来源 | 加载方式 |
|---|---|---|
| 计划执行纪律（逐任务、检查点、不跳步） | superpowers `executing-plans` 的等价纪律 | 若 Qoder 支持自定义 skill/规则：把 `docs/superpowers/plans/…md` 的任务清单作为执行序列绑定；把本文件 §0/§3/§5 存为项目 Rule（如 `.qoder/rules/excel-track-a.md`） |
| TDD 工作法 | superpowers `test-driven-development` 的等价纪律 | 同上，§3 第 1 条即其压缩版 |
| 代码库事实 | 本文件 §2.2 | 无需再派探索任务——file:line 已核实 |
| **不需要** | brainstorming / 设计类 skill | **设计已冻结**。Qoder 若提出改 schema/改架构的意见：记录到移交反馈，不实施 |

## 5. 交付产物（DoD）

### 5.1 代码（新建 9 个文件）

```
src/document2chunk/extractors/excel/__init__.py
src/document2chunk/extractors/excel/models.py        # 数据模型 + SheetRoute
src/document2chunk/extractors/excel/reader.py        # calamine + openpyxl 双 load 读层
src/document2chunk/extractors/excel/boundary.py      # 空行横切+空列纵切
src/document2chunk/extractors/excel/header.py        # 表头检测 + 路线分流
src/document2chunk/extractors/excel/flatten.py       # 扁平化 + 合并下放
src/document2chunk/extractors/excel/values.py        # 值规范化
src/document2chunk/extractors/excel/totals.py        # 合计行打标
src/document2chunk/extractors/excel/parser.py        # 编排（毒输入兜底）
src/document2chunk/extractors/excel/serializer.py    # row_text + to_envelope
src/document2chunk/extractors/excel/csv_reader.py    # csv 轻量路径
```

（`__init__.py` + 10 个模块；全部代码在实施计划 Task 1–9 内给出，照抄级可执行。）

### 5.2 代码（修改 4 个文件）

| 文件 | 改动 |
|---|---|
| `pyproject.toml` | extras 增 `excel = ["python-calamine>=0.7.0", "openpyxl>=3.1.0"]` |
| `src/document2chunk/exceptions.py` | 增 `ExcelParseError(Document2ChunkError)` |
| `src/document2chunk/serve.py` | 增 `parse_excel_to_envelope`（指纹路由+soffice 归一化）；`_xls_err` 文案改指 `/parse-excel` |
| `src/document2chunk/api.py` | `create_app()` 内增 `POST /parse-excel`（multipart `file`/`file_path`，并发信号量+线程池） |
| `Dockerfile.d2c` | extras 追加 `excel` |

### 5.3 测试（新建 9 个 + 修改 1 个，全部代码在计划内）

```
tests/_excel_fixtures.py            # 运行时生成 xlsx 夹具（openpyxl），对齐 tests/_fixtures.py 模式
tests/test_excel_models.py          tests/test_excel_reader.py
tests/test_excel_boundary.py        tests/test_excel_header.py
tests/test_excel_flatten.py         tests/test_excel_values.py
tests/test_excel_totals.py          tests/test_excel_parser.py
tests/test_excel_csv.py             tests/test_excel_serve.py
tests/test_parse_excel_api.py       tests/test_excel_corpus_smoke.py   # 真实语料，本机无则 skip
tests/test_serve_legacy.py          # 修改：xlsx/xls 400 断言改新文案（:100-108 两个用例）
```

### 5.4 验收标准（全绿才算交付）

1. `uv run -m pytest -q` 全量通过，既有测试零回归；
2. 真实语料冒烟（`tests/test_excel_corpus_smoke.py`）：P0 5 件全产出非空 rows/text；假 .xlsx（`20- 软弱地层…xlsx`）经 soffice 归一化可解析（无 soffice 则 skip）；
3. 对照组不误判：通讯录类（`集团所属组织页面数据.xlsx`）不得产出多行表头键（键数=列数）；
4. 键形抽查：多级表头样本（`项目自定义导出数据 (基础)-最新.xlsx`）出现 `华东-子列1` 风格键、撞名`（2）`、空表头`列N`；
5. 合计行：`7-广东省…决算表.xlsx` 的合计行 `meta.from_total == true` 且仍在 rows 中（轨 A 保留）；
6. 警告正确：外部链接/透视表/隐藏 sheet/无缓存公式各产生对应 warning 文案。

## 6. 文档体系（本次任务全景）

```
决策层 ─ Document2Chunk-Excel解析技术调研报告.md     （双轨架构+全部设计决策，定稿）
   │
定档层 ─ Document2Chunk-Excel难点定档表.md           （28 难点 P0/P1/P2 + 用例映射 + 合成用例清单）
   │
证据层 ─ Excel语料盘点-draft.md                      （kxx-docs 23 文件频次 + 逐文件测试理由）
   │        └─ _forensics/excel_corpus_scan.json     （扫描明细）
   │        └─ scripts/_corpus_scan_excel.py         （扫描脚本，可复扫）
   │
执行层 ─ docs/superpowers/plans/2026-09-20-excel-track-a-chunk2embedding.md
   │        （11 任务 TDD 实施计划：完整代码+验证命令+commit 序列——Qoder 的唯一编码依据）
   │
移交层 ─ Document2Chunk-Excel轨A移交方案.md          （本文档：入口/上下文/纪律/DoD/文件清单）
   │
二期   ─ Document2Chunk-text2sql数据准备调研-draft.md （轨 B 前置调研，本次不实施）
```

- 读者与时机：1–4 给"想理解为什么"的人；5（执行层）给编码代理；本文档给移交操作者。
- 状态标记：决策层/定档层/证据层均为**定稿**；执行层**定稿待执行**；二期文档为**草稿待评审**。

## 7. 验收后回填（Qoder 交付后人工/主会话动作）

- `Excel语料盘点-draft.md` → 转`-v1`正稿（去 draft）；定档表 §3 合成用例随回归库补齐逐项勾选；
- 调研报告 §5.1 增补"轨 A 已交付 commit 范围"一行；
- 轨 B 立项评审 `Document2Chunk-text2sql数据准备调研-draft.md`。

## 8. 明确不做（防 scope creep，见调研报告 §7 与计划"明确不做"）

不接入 Document IR/postprocess；不做 chunk 切分与 embedding（下游职责）；不做轨 B 增量（类型推断/DDL/合计剔除）；不做整表 HTML/Markdown 输出；不解密加密文件（拒收政策）；不重设计已定稿 schema。
