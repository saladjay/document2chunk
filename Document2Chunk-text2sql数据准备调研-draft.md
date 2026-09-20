# Document2Chunk — text2sql 数据准备调研草稿（轨 B 子文档）

> 调研日期：2026-09-20。范围：**仅数据准备侧（非 LLM）**——把 Excel/CSV 变成 text2sql 友好的干净表结构。
> NL→SQL 的模型/提示词工程不在本篇范围（第 5 节引用 BIRD/Spider 仅取其"数据准备惯例"）。
> 关联：`Document2Chunk-Excel解析技术调研报告.md` §2.1 轨 B 行、§7.6（"轨 B 二期"前置调研）。
> 调研方法：中英文 Web 检索 + **DuckDB 1.5.5 / SQLite 3.50.4 本地实测**（文中标注"本地实测"者为第一手验证，非转述）。

---

## 1. 结论摘要

| # | 调研问题 | 结论（2-3 句） |
|---|---|---|
| 1 | 类型推断 | 主流是"采样 + most-specific-wins 逐级提升"：DuckDB sniffer（默认采样 20480 行，候选序 NULL→BOOLEAN→BIGINT→DOUBLE→TIME→DATE→TIMESTAMP→VARCHAR）、csvkit `csvsql --snifflimit`、pandas 逐 chunk 推断、frictionless `Schema.infer()`。降级路径三档：高置信直接定类型；长尾异常值保留类型但记 warning（DuckDB `store_rejects` 思路）；低置信降级 TEXT——这正是 Excel 报告 §2.1 已定的"低置信降级 TEXT + warning（不硬猜）"，业界一致。 |
| 2 | 合计行/小计行 | text2sql 场景合计行是**答案正确性问题**（`SUM()` 双计），不是噪音问题，必须剔除。识别靠五类启发式叠加：行首列命中词表（合计/总计/小计/Total/Grand Total）、维度列空 + 度量列非空、校验和验证（该行数值 = 上方若干行之和）、位置（块尾/空行分隔）、Excel 分组大纲级别（`outlineLevel`，内置分类汇总有结构痕迹）。手打合计行无结构痕迹，只能词表+校验和。 |
| 3 | 中文列名/表名 | SQLite 与 DuckDB（1.5.5 本地实测）都**原生支持中文标识符**，双引号/方括号/反引号三种写法皆可，甚至不加引号也合法。SQLite 有个 text2sql 杀手坑：双引号字符串回退——`SELECT "不存在列"` 不报错而是当字符串字面量返回（本地实测复现）；DuckDB 无此回退，直接 BinderException（更安全）。结论：**保留中文列名 + 全程双引号纪律**优于英文映射（英文映射需维护对照表，反而加重 schema linking 负担）。 |
| 4 | schema linking 数据准备 | BIRD 每库附带 `database_description/`（每表一个 CSV：Original Column Name / Column Name / Data Type / Value Description / Column Description）+ 每题专家 `evidence`。业界惯例三件套：CREATE TABLE DDL（含外键）+ 列描述 + **值采样**（`SELECT DISTINCT col … LIMIT R`，普遍取每表 3 行，元组列表放 SQL 注释里）。这三件套全部可以在数据准备侧离线固化。 |
| 5 | 现成工具链 | 没有成熟的"xlsx→SQL 库"一体化 OSS：csvkit 只吃 CSV、sqlite-utils 只吃 CSV/TSV/JSON、pandas `to_sql` 类型映射粗、DuckDB excel 扩展最接近但类型推断只看首条数据行。**数据准备主体仍需自研**（与 Excel 报告 §7.3 决策一致），DuckDB 可作为"第二推断引擎/验收 harness"对拍。 |
| 6 | Excel 特有问题 | RAG 轨要求 chunk 自包含（值下放每行）；入库轨要求 1NF/tidy data（单行表头、无合并单元格、一列一变量）。数据区纵向合并单元格的入库惯例是**下放值（denormalize）保持单表可查**，不强行提维表；多级表头按"父-子路径"或"叶子+前缀"两种方式扁平化；宽表（月份即列）是否 UNPIVOT 取决于查询面——text2sql 下 wide 表常反而更好查，不必强制 melt。 |

---

## 2. 类型推断（Q1）

### 2.1 四个成熟实现

**（1）DuckDB CSV Sniffer —— 采样 + 逐级提升（工业最强，值得当范本）**
官方博客（Holanda, 2023-10-27）披露的完整流程，五阶段：Dialect → Type Detection → Header Detection → Type Replacement → Type Refinement。

- 类型候选**按特异性排序**：`SQLNULL → BOOLEAN → BIGINT → DOUBLE → TIME → DATE → TIMESTAMP → VARCHAR`，逐值试 cast、失败即从候选数组剔除，整块扫完后剩下最窄的类型。
- 默认采样 `sample_size = 20480`（10 个 chunk），可设 `-1` 全量；`auto_type_candidates` 可收缩候选集（默认 `['NULL','BOOLEAN','BIGINT','DOUBLE','TIME','DATE','TIMESTAMP','VARCHAR']`）。
- **Type Refinement**：向文件后部继续向量化试 cast，发现不符就放宽（例：BOOL 列里混入 `"N/A"` → 升级为 VARCHAR）——这就是"推断失败的自动降级"，方向是**往宽降、不硬猜**。
- **Header Detection 的已知弱点**（官方 future work）：全字符串列时靠"首行 cast 不匹配"判表头会失效；多段采样（文件中后段）也是官方承认的待办 → 说明"只看头部采样"是行业通病，我们的采样应**分段**。
- 设计哲学：宁可抛错也不返回"极不可能"的检测结果；`all_varchar=true` 是官方逃生门（先全文本入库、再显式 CAST）。
- 显式覆盖参数：`types`/`dtypes`、`columns`、`dateformat`/`timestampformat`、`thousands`、`decimal_separator`、`nullstr`、`store_rejects`（坏行进 rejects 表，不中断）。

**（2）csvkit `csvsql` —— 保守 + 采样上限**
`csvsql --insert/--create` 直接生成建表与插入语句；类型集为 INTEGER / DECIMAL / DATE / DATETIME / VARCHAR / BOOLEAN；`--snifflimit N` 控制采样行数。已知短板：倾向保守出 VARCHAR（慢但安全），稀有值出现在文件后段时会被误型——所以官方给了 `--snifflimit` 而不是全量扫。适合当"对拍引擎"，不适合当主推断。

**（3）pandas —— 便利但坑多（读入即定型，定型即不可逆）**
- `read_csv` 大文件默认 `low_memory` 行为导致**chunk 间 dtype 不一致**（`DtypeWarning`）；生产管道要求显式 `dtype=` + `parse_dates=`。
- `read_excel` 默认逐列推断，混合类型列落成 `object`；`dtype='object'` 可保留 Excel 原值（不做 dtype 解释）——这正是"轨 B 要原始值"的读法。
- `dtype_backend='pyarrow'` 有已知 bug：PyArrow 按首元素严格定型，后续元素不匹配即崩（GitHub issue，2026-01）→ **首行定型不可靠**再次得到印证。
- `to_sql` 类型映射粗（object → TEXT），入库前需 `dtype=` 显式固化。

**（4）frictionless Table Schema —— 推断产物可序列化（对"交付 schema"很有参考价值）**
`Schema.infer()` 从 CSV 自动生成 Table Schema 描述符（JSON），类型集 `string/integer/number/boolean/object/date/time/datetime/year/...`，且 `decimalChar`/`groupChar`/`missingValues` 可配——**推断结果是标准化的 JSON schema 文件**，天然适合作为轨 B 的"DDL 建议 + 类型标注"交付物形态。`pandera.infer_schema()` 同理（DataFrame → DataFrameSchema，可继续加 `unique`/`in_range`/`nullable=False` 约束后固化），偏 Python 代码侧。

### 2.2 降级策略的共识（可直接落进设计）

| 置信 | 判定 | 处置 |
|---|---|---|
| 高 | 采样（含中后段）全部可 cast，无空值歧义 | 定型，DDL 直接用 |
| 中 | 主体可 cast，少数值失败（长尾） | **保留类型** + 违规值/行号进 warnings（参照 DuckDB `store_rejects`：坏行进 rejects 表而非报错中断） |
| 低 | 空值占比高/多格式混杂/首行即异常 | 降级 TEXT + warning，不硬猜（Excel 报告 §2.1 已定，业界一致） |

三条硬规则（多篇来源交叉）：
1. **ID/工号/电话/邮编/前导零列永远 TEXT**——Excel 15 位精度截断、前导零丢失是 C 组难点 13 的同源问题；数字型 ID 入库后在 `GROUP BY`/join 语义上会出错。
2. **单位/币种不进数值**：`¥1024.50`、`12%` 要么转纯数值 + 单位列，要么整列 TEXT（轨 B 按报告 §2.1 取前者：原始值+类型）。
3. **日期走 ISO**：DuckDB 候选格式只有 `%m-%d-%Y`/`%d-%m-%Y`/`%Y-%m-%d` 及两位年变体，`2023年12月1日`、`2023/12/1` 都不在搜索空间内——中文 Excel 日期**必须自研解析**，不能指望通用 sniffer。

---

## 3. 合计行 / 小计行清洗（Q2）

### 3.1 为什么在 text2sql 是 P0

轨 A 保留打标即可（合计行对 embedding 无害甚至有信息量）；轨 B 必须剔除——用户问"总销售额多少"，模型生成 `SELECT SUM(金额)`，若表里混着一条"合计 20000"，答案直接翻倍。这与 Excel 报告 §2.1"合计行/小计行：必须剔除（`SUM()` 双计=答案错误）"完全一致，且是 ETL 领域共识（Power Query 教程明确：透视前必须过滤掉源数据里的合计行，否则**双重计数**）。

### 3.2 合计行的三种来源与可识别度

| 来源 | 结构痕迹 | 识别难度 |
|---|---|---|
| Excel 内置"分类汇总"（Data → Subtotal） | 有：分组行带 `SUBTOTAL()` 公式、行带大纲级别 `outlineLevel>0`、可一键"全部删除" | 易（程序可读 outlineLevel） |
| 透视表 Grand Total / 表尾合计 | 弱：只是普通行，位置固定在块尾 | 中 |
| 手打的"合计/总计"行 | 无 | 难（只能启发式） |

注意：我们走 calamine/openpyxl 读值，`SUBTOTAL()` 公式读到的仍是缓存值，但 openpyxl 能拿到公式文本与 `outlineLevel`，可作为强信号。

### 3.3 识别启发式清单（建议按此顺序叠加，命中即标）

1. **词表命中**：行首列（第一非空维度列）文本 ∈ {合计, 总计, 小计, 汇总, 累计, Total, Subtotal, Grand Total, Σ}；正则如 `str.contains('^(合计|总计|小计|汇总|累计|.*Total)')`。注意只匹配**行首列**，避免把列名叫"合计金额"的数据行误杀。
2. **结构特征**：维度列（字符串列）为空 / 度量列（数值列）非空；行内多数列空。
3. **校验和验证**：该行度量值 ≈ 上方连续若干行之和（分组小计）或 ≈ 全表之和（总计）。这是唯一能"证明"它是合计行的方式，也是剔除后回归验证的手段（剔除前后 `SUM` 对账）。
4. **位置特征**：位于 subtable 最后一行、或其后紧跟空行/表尾备注（共享前端的表边界切分信息直接可用）。
5. **格式信号**（需 openpyxl，二遍读取已有）：加粗/填充色/上边框合计线、`outlineLevel>0`。

### 3.4 处置政策（对齐轨 B 设计）

- 剔除，不进表数据；剔除的行内容 + 命中原因 + 行号进 `warnings`（Excel 报告 §2.1 已定"剔除记 warnings"）。
- 分级小计（地区小计 + 全国总计）需按"校验和层级"分别剔除，否则部分剔除仍会双计。
- 剔除后输出 `removed_total_rows: N` 与 `checksum_ok: bool`，供下游验收。
- ETL 工具界的同款思路：Power Query/Alteryx 社区做法是先加 "Row Type" 辅助列打标再过滤，或"先从小计行抽取信息（如组名）再删"——对应到我们就是**先打标、后剔除、打标信息落 warnings**。

---

## 4. 中文列名 / 表名（Q3）

### 4.1 引擎支持与引号规则（含本地实测）

| 引擎 | 中文标识符 | 引号规则 | 双引号陷阱 |
|---|---|---|---|
| **SQLite** 3.50.4 | ✅ 不加引号也合法（中文非关键词） | 四种写法：`'x'`=字符串字面量；`"x"`=标识符（标准）；`[x]`=标识符（MSSQL 兼容）；`` `x` ``=标识符（MySQL 兼容） | ⚠️ **双引号字符串回退**：`"不存在列"` 不报错，静默当字符串字面量返回（本地实测复现） |
| **DuckDB** 1.5.5 | ✅ 本地实测：`CREATE TABLE 员工表 ("姓名" VARCHAR, "总 金额" DOUBLE)` 成功；`SELECT 姓名` 不加引号也成功 | 双引号 = 标识符（标准 SQL） | ✅ 无回退：错误标识符直接 `BinderException`（错误早暴露，**对 text2sql 更安全**） |
| MySQL 8 | 支持 | 默认双引号是**字符串**，需 `ANSI_QUOTES` 才当标识符；标识符 ≤64 字符 | 方言差异是跨库生成的坑 |

本地实测（SQLite）还确认：含空格的 `"总 金额"`、保留字 `"order"` 加引号后均可用——即中文列名不受"必须合法标识符"限制，**列名清洗只需处理撞名/空名/换行，不必转拼音或译英文**。

**DuckDB 专属坑（本地实测）**：`read_csv(..., normalize_names=true)` 会把中文表头剥成 `_`、`__1`、`__2`（非字母数字字符全删）——**中文入库场景禁止使用 `normalize_names`**。

另一个 SQLite 侧的防线：双引号回退可用 `SQLITE_DBCONFIG_DQS_DML`/`DQS_DDL` 开关关闭（见 sqlite.org/quirks.html "Double-Quoted String Literals Are Accepted"）——若下游用 SQLite 执行生成的 SQL，建议在初始化时关闭，把"幻觉列名"从静默错答变成显式报错。

### 4.2 中文列名 vs 英文映射的讨论

| | 保留中文列名 | 英文映射（列名→拼音/英文） |
|---|---|---|
| schema linking | ✅ 用户问题词与列名字面直接匹配（"销售额" ↔ `销售额`），无需跨语言跳变 | ❌ 模型必须先完成"用户中文词→英文列名"翻译，这一步正是 schema linking 的主要失分点 |
| 维护成本 | ✅ 无需映射表 | ❌ 映射表本身要维护、要入库、要进 prompt |
| SQL 合法性 | 需要引号纪律（全程 `"..."`） | ✅ 更通用 |
| token 成本 | 中文列名 token 通常 ≤ 等义英文短语 | 略省 |
| 生态兼容 | BI/ETL 工具对中文列名兼容一般 | ✅ |

中文 text2sql 实践（知乎优化文、AWS Bedrock 中文场景、OceanBase Text2SQL）的共同做法是：**以"元数据检索"桥接**——把「中文业务词 ↔ 列名 ↔ 注释」三元组向量化，查询时先检索相关 schema 再注入 prompt；即中文列名不是问题，缺的是**注释与值采样**。BIRD 的设计佐证了"保留原名 + 注释"路线：`database_description` CSV 同时给 Original Column Name 与 Column Name（缩写/规范化名）两列，本质就是"原名 + 可读注释"双轨，而不是把原名改掉。

**结论**：轨 B 保留中文列名（与 Excel 报告 §2.1"表名 = sheet 名（冲突加序号，中文加引号）"一致），把注释/别名列作为 schema 交付物的一部分，而不是做英文映射。

---

## 5. schema linking 的数据准备惯例（Q4，只取数据侧）

### 5.1 BIRD 的数据组织（事实标准）

BIRD（NeurIPS 2023，12,751 对 question-SQL，95 库 33.4GB）每个数据库目录两件东西：

- `database_description/`：每表一个 CSV，官方 README 原文 "the csv files are manufactured to describe database schema and its values for models to explore or references"。字段（NeurIPS 2023 论文示例）为 **Original Column Name / Column Name / Data Type / Value Description / Column Description**——即"类型 + 值说明 + 列说明"三要素。
- `sqlite/` 库本体。
- 每条样本：`db_id / question / evidence / SQL`；`evidence` 是专家标注的**外部知识**（业务规则、计算口径），与 schema 元数据是两回事（SEED 论文明确区分）。

### 5.2 惯例三件套与关键经验值

| 元素 | 惯例做法 | 出处 |
|---|---|---|
| DDL | `CREATE TABLE`（含外键），代码化 schema 表示是主流；DAIL-SQL 系统比较了 question/schema 表示对效果的影响（VLDB 2024） | DAIL-SQL |
| 列描述 | 列名 + 中文/业务注释；"Synthetic SQL Column Descriptions"（NeurIPS 2024）证明补充列描述能解决列名歧义问题 | arXiv 2408.04691 |
| 值采样 | `SELECT DISTINCT [col] FROM [table] LIMIT R`（arXiv 2305.11853 提出）；主流 prompt 取**每表 3 行**，格式为 SQL 注释内的元组列表，如 `/* Sample rows: customers: [(3,'SME','EUR'), ...] */`（MCS-SQL 同款 "Sample rows of each table in csv format"） | arXiv 2305.11853、DAIL-SQL、MCS-SQL |
| 值检索/列裁剪 | CHESS：关键词抽取 → schema filtering → value retrieval（LSH 相似度取与问题词相近的值）；RSL-SQL：双向 schema linking 把每查询平均列数砍到 13 | arXiv 2405.16755 / 2411.00073 |

**经验值的含义**：3 行采样是性价比拐点——太少无法区分 `2023-01-01` 与 `20230101` 这类格式歧义，太多占爆 prompt。值采样还有一个隐性作用：**值本身就是列语义的最好注释**（BIRD 自带列描述不足时，多篇工作都退回看 sample rows）。

### 5.3 对"数据准备侧"的可固化产物

以上全部可离线生成，不需要 LLM：
1. 每列 distinct 值采样（≤3 个，跳过超长文本，取等值分布里的代表值）；
2. 每列类型 + 推断置信度；
3. 每表行数、主键建议（唯一性检测）；
4. 弱外键提示：同名列 + 类型一致 + 值域重叠（Excel 没有 FK 约束，这是唯一可给的 join 线索）。

---

## 6. 现成工具链（Q5）

| 工具 | 能力 | 优势 | 对轨 B 的短板 |
|---|---|---|---|
| **csvkit `csvsql`** | CSV → SQL（建表+插入），可直连数据库 | 方言齐全、`--snifflimit` 可控 | 只吃 CSV；推断偏保守 VARCHAR；无 Excel 结构处理 |
| **DuckDB `read_csv` / `read_xlsx`** | 直接查询/建表（`CREATE TABLE t AS SELECT * FROM read_csv(...)`）；excel 扩展 `read_xlsx` 支持 `header/sheet/range/all_varchar/ignore_errors/empty_as_varchar/stop_at_empty` | **类型推断最强**（见 §2.1）；一个进程内完成"读文件+建表+校验"；.xls 不支持（正好接我们的 soffice 归一化链路） | xlsx 类型推断**只看第一条数据行**（官方 README 明说），首行空 cell 即翻车（靠 `empty_as_varchar`/`ignore_errors` 补救）；合并单元格/多级表头完全不处理 |
| **pandas `to_sql`** | DataFrame → 任意 SQLAlchemy 目标库 | 生态最广 | 慢；object 列一律 TEXT；必须先 `dtype=` 固化 |
| **sqlite-utils** | `sqlite-utils insert data.db t data.csv --csv` 自动建表；支持 CSV/TSV/JSON/NL-JSON | 一条命令入库、schema 干净、附带 FTS；**最适合当验收基线** | 不吃 xlsx（需先转 CSV/JSON）；类型推断有限（数值/文本二分） |
| **csvs-to-sqlite** | Willison 旧工具 | — | 官方已建议改用 sqlite-utils |
| **datasette-excel** | Datasette 插件 | — | 方向相反（SQLite→xlsx 导出），仅说明生态里"xlsx 入库"无人做 |
| **frictionless / pandera** | 推断 + 校验，产物是标准 schema 描述符 | schema 可序列化、可复检 | 不负责入库；多一道依赖 |
| **散装脚本**（openpyxl + sqlite3 的 gist 类） | — | — | 无推断、无边界/表头处理，不可作为产品路径 |

**判断**：没有现成的"Excel → text2sql 就绪库"一体化 OSS——结构识别（边界/表头/合并）+ 类型推断 + 合计行清洗 + schema 描述产物，这四件事被分散在四类工具里。**主体自研成立**；DuckDB 的定位是①第二推断引擎做交叉对拍，②入库验证 harness（`DESCRIBE` + 抽样聚合对账），③`.csv` 轻量路径的候选引擎。

---

## 7. Excel 特有问题：合并单元格 / 多级表头（Q6）

### 7.1 入库场景与 RAG 场景的目标差异

| | 轨 A（RAG/chunk2embedding） | 轨 B（text2sql 入库） |
|---|---|---|
| 表头形态 | 路径扁平化为 JSON 键（`部门-一季度-营收`），多行表头自然消解 | 必须**单行表头**（1NF/tidy data），列名即列标识 |
| 合并单元格（数据区） | 值下放每行（chunk 自包含原则，已定 Q4） | 同样**下放**（denormalize），保持单表可查，避免模型写不出 join |
| 列语义 | 键名只要人能读 | 列名 + 注释 + 类型三件套 |
| 规范化程度 | 无所谓 | 1NF 起步；是否提升到 2NF/维表视查询面 |

规范侧的权威依据是 Broman & Woo《Data Organization in Spreadsheets》（2018）与 Wickham《Tidy Data》：一列一变量、一行一观测、一格一值、无合并单元格、无数据区内空行空列、日期统一格式、不要用颜色/格式承载语义。中文社区同源表述即"一维表/1NF"（标题行只占一行、不合并单元格、必须有主键字段）。

### 7.2 多级表头的两种入库扁平化惯例

1. **拼接法**：`父-子` 连接（`华东-营收`）——轨 A 已用，轨 B 也可用（列名保持中文、连接符统一 `-`），成本最低。
2. **父子两列法**：拆成 `华东_营收` 或 `华东/营收` 两级列名（BIRD 风格的 original column name 也可以承载这一层语义进注释）。
   - 对 text2sql，拼接法更好：模型只需匹配一个列名；两列法引入"列名里还有层级"的额外约定。
3. 实现要点（与共享前端一致）：合并单元格只有左上角有值（openpyxl 中其余是 `MergeCell`，值为 None）→ 表头各层 `ffill()` 还原归属；readxl 多行表头的社区共识同样是"表头区与数据区分开读，再合并"。

### 7.3 宽表要不要 UNPIVOT（值得明确的取舍）

`月份/季度作为列`（一月、二月…）在数据库规范里是"变量嵌在表头"，规范做法是 UNPIVOT 成 `月份, 值` 两列（Excel 侧 Power Query 逆透视、pandas `melt`、DuckDB `UNPIVOT`）。但对 text2sql：
- wide 表的查询（`SELECT 一月 FROM t`）对 LLM 反而**更简单**，long 表要生成带 `PIVOT`/`CASE WHEN` 的列转行 SQL，更难；
- 只有当问题面要求"跨月份聚合/按月份过滤"时，long 表才明显占优。

**建议**：默认保留 wide + 在 schema 描述里注明"各月份列同质，数值列"，不做强制 melt；作为 P2 可选项记录。

### 7.4 读取层事实（与共享前端的接口）

DuckDB `read_xlsx` 不解析合并单元格（合并区除左上格外读空），也不能展开多级表头——所以"合并还原/表头扁平化"必须发生在我们的共享前端（openpyxl `merged_cells.ranges` + `ffill`，Excel 报告 §3.4 已定），轨 B 只消费扁平化结果。这是一个明确的分工边界：**结构问题全部在共享前端解决，轨 B 不再碰 cell 网格**。

---

## 8. 对我们双轨架构的启示（轨 B 在共享前端之上的额外步骤）

共享前端已产出：原始值/显示值、subtable 边界、表头检测、表头路径扁平化、合并单元格还原（下放）、`from_total` 行标、隐藏行列 flag。轨 B 需要叠加以下 9 步（按流水线顺序）：

1. **值形态切换（已定，落点要固化）**：从"显示值"切回 calamine 原始值 + number format；`¥1024.50` → `1024.5` + 货币信息进列注释。轨 A/B 两路值都要在序列化层保留。
2. **合计行从"打标"变"剔除"**：`from_total=true` 直接不进表数据；剔除原因/行号/校验和结果进 `warnings`；输出 `removed_total_rows` 与 `sum_reconciliation`（剔除后 SUM 与原表总计行对账）。识别用 §3.3 的五层启发式（词表只匹配行首列）。
3. **类型推断引擎（自研 + 双引擎对拍）**：分段采样（头 + 中 + 尾各 ≥2048 行，吸取 DuckDB 官方承认的"只看头部"弱点）+ 候选序 `NULL→BOOLEAN→INT→FLOAT→DATE→TIMESTAMP→TEXT` + 中置信记 warning/低置信降级 TEXT；ID/电话/前导零列白名单强制 TEXT；中文日期格式自研解析。自研结果与 DuckDB `DESCRIBE SELECT * FROM read_csv(all_varchar=false)` 对拍，不一致列人工复核。
4. **DDL 生成（SQLite/DuckDB 方言）**：标识符全程双引号；**禁用 `normalize_names`**（中文列名会被剥成 `_`）；表名 = sheet 名（冲突加序号，中文加引号，§2.1 已定）；撞名/空表头沿用轨 A 规则（`列N`）。
5. **schema 描述产物（BIRD 三件套，轨 B 交付物具体化）**：每表输出 —— 表名/sheet 名/行数；列清单（**列名 + 类型 + distinct 值采样 ≤3 + 注释[表头路径父层语义] + 置信度**）；主键建议（唯一性检测）；弱外键提示（同名列 + 类型一致 + 值域重叠）。格式建议 frictionless Table Schema（JSON）+ 一段 DDL，前者机器可复检，后者直接可贴 prompt。
6. **空值五义统一**：None/空串/空格/`""`/合并阴影 → SQL NULL（§2.1 已定"值为 null → NULL"）；文本型 `"N/A"`/`"-"` **保留原值**并进列注释，避免误转 NULL 破坏 `COUNT` 口径。
7. **隐藏行列开关**：默认剔除隐藏列（§2.1 已定），开关 + 剔除记录。
8. **入库验证 harness**：sqlite-utils（或 DuckDB）真实建库 + 插入全量 + 三条抽样聚合（COUNT/SUM/MIN-MAX）与源表合计行对账 + `DESCRIBE` 类型抽查；沿用现有 30MB 语料 harness 与 subprocess 看门狗经验（issues7/S-2 已有先例）。
9. **下游注意事项文档（非代码交付）**：写明 SQLite 双引号回退陷阱（建议下游 `SQLITE_DBCONFIG_DQS_DML=0`）、DuckDB 无回退、MySQL 需 `ANSI_QUOTES`、中文标识符必须双引号。

一句话版：**共享前端管"结构正确"，轨 B 在此之上管"语义与类型正确"——合计行剔除、类型推断降级、BIRD 式 schema 描述、双引号纪律、入库对账，这五件是轨 A 完全不需要的增量。**

---

## 9. 参考资料

**类型推断**
- DuckDB, "DuckDB's CSV Sniffer: Automatic Detection of Types and Dialects"（五阶段流程、候选序、sample_size=20480、Type Refinement、future work）: <https://duckdb.org/2023/10/27/csv-sniffer>
- DuckDB, CSV Import 文档（`sample_size`/`all_varchar`/`auto_type_candidates`/`store_rejects`/`nullstr`/`thousands`/`dateformat` 全参数）: <https://duckdb.org/docs/current/data/csv/overview.html>
- csvkit, `csvsql` 文档（`--insert`/`--snifflimit`，类型集）: <https://csvkit.readthedocs.io/en/latest/scripts/csvsql.html>
- pandas, `read_excel` API（默认推断 / `dtype='object'` 保留原值）: <https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html>
- pandas, `read_csv` API（`low_memory`/`dtype`/`parse_dates`）: <https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html>
- Frictionless Table Schema 规范（类型集、`missingValues`、`decimalChar`/`groupChar`）: <https://specs.frictionlessdata.io/table-schema/>
- Frictionless Framework, Schema（`Schema.infer()`）: <https://framework.frictionlessdata.io/docs/framework/schema.html>
- frictionlessdata/tableschema-py: <https://github.com/frictionlessdata/tableschema-py>
- pandera, DataFrame Schemas（`infer_schema`）: <https://pandera.readthedocs.io/en/stable/dataframe_schemas.html>

**合计行/小计行与数据规范**
- Broman K W, Woo K H. *Data Organization in Spreadsheets*. The American Statistician 72(1), 2018（tidy/machine-readable 布局准则）: <https://doi.org/10.1080/00031305.2017.1375989>
- Wickham H. *Tidy Data*. J. Stat. Software 59(10), 2014: <https://www.jstatsoft.org/article/view/v059i10>
- Microsoft Support，删除工作表数据列表中的分类汇总（内置 Subtotal 的"全部删除"）: <https://support.microsoft.com/zh-cn/office>（检索词"删除分类汇总"）
- Alteryx Community，Extract "extra" info from subtotal rows then remove（先抽信息再删小计）: <https://community.alteryx.com>
- Macrordinary，Power Query 过滤源数据中的合计行防双重计数: <https://macrordinary.ca>

**中文标识符与引号规则**
- SQLite, Keywords（四种引号写法）: <https://sqlite.org/lang_keywords.html>
- SQLite, Quirks → "Double-Quoted String Literals Are Accepted"（双引号回退陷阱与 DQS 开关）: <https://sqlite.org/quirks.html>
- MySQL 8.0 Reference Manual, Identifiers（`ANSI_QUOTES`、64 字符上限）: <https://dev.mysql.com/doc/refman/8.0/en/identifiers.html>
- DuckDB Excel 扩展 README（`read_xlsx` 参数表、类型推断只看首条数据行）: <https://github.com/duckdb/duckdb-excel>（原文 <https://raw.githubusercontent.com/duckdb/duckdb-excel/main/README.md>）
- DuckDB, Excel Extension 文档: <https://duckdb.org/docs/current/core_extensions/excel.html>
- DuckDB, Excel Import 指南（.xls 不支持）: <https://duckdb.org/docs/lts/guides/file_formats/excel_import.html>
- 本地实测（第一手）：SQLite 3.50.4 / DuckDB 1.5.5 中文标识符与引号行为、`normalize_names` 剥中文表头、双引号回退差异。

**schema linking 数据准备（BIRD/Spider 惯例）**
- BIRD-SQL Mini-Dev 仓库（`database_description/` CSV、"describe database schema and its values"、`evidence` 字段）: <https://github.com/bird-bench/mini_dev>
- Li J et al. *Can LLM Already Serve as A Database Interface? (BIRD)*. NeurIPS 2023: <https://arxiv.org/abs/2305.03111>
- Gao Y et al. *Text-to-SQL Empowered by LLMs: A Benchmark Evaluation (DAIL-SQL)*. VLDB 2024（schema 表示方式系统比较、Table Values Representation）: <https://arxiv.org/abs/2308.15363> / 代码 <https://github.com/BeachWang/DAIL-SQL>
- Chang S, Fosler-Lussier E. *How to Prompt LLMs for Text-to-SQL: A Study in Zero-shot, Single-domain, and Cross-domain Settings*（`SELECT DISTINCT col … LIMIT R` 值采样）: <https://arxiv.org/abs/2305.11853>
- Talaei S et al. *CHESS: Contextual Harnessing for Efficient SQL Synthesis*（关键词抽取 + schema/value filtering + value retrieval）: <https://arxiv.org/abs/2405.16755>
- Cao Y et al. *RSL-SQL: Robust Schema Linking in Text-to-SQL Generation*（双向 schema linking，均 13 列/查询）: <https://arxiv.org/abs/2411.00073>
- Shkapenyuk V et al. *Synthetic SQL Column Descriptions and Their Impact on Text-to-SQL Performance*. NeurIPS 2024（列描述缓解列名歧义）: <https://arxiv.org/abs/2408.04691>
- Vanna AI（DDL / documentation / question-SQL 三类训练计划）: <https://github.com/vanna-ai/vanna>

**工具链**
- sqlite-utils 文档（`insert --csv/--tsv/--json` 自动建表）: <https://sqlite-utils.datasette.io/en/stable/cli.html#inserting-csv-or-tsv-data> / 代码 <https://github.com/simonw/sqlite-utils>
- datasette-excel（SQLite→xlsx 导出，反向插件）: <https://pypi.org/project/datasette-excel/>
- MotherDuck, *DuckDB Excel Extension: How to Read & Import XLSX Files*（2025-05）: <https://motherduck.com/blog/duckdb-excel-extension/>
- python-calamine（PyPI）: <https://pypi.org/project/python-calamine/>

**合并单元格 / 多级表头入库**
- openpyxl, merged cells API（合并区非左上格为 `MergeCell`，值为 None）: <https://openpyxl.readthedocs.io/en/stable/api/openpyxl.worksheet.merge.html>
- tidyverse/readxl issue #379 "Multi line headers"（表头区与数据区分开读的社区惯例）: <https://github.com/tidyverse/readxl/issues/379>
- 知乎，《Excel 数据处理 | 双层表头的表格 → 规范化为一维表》（Alt+D+P / Power Query 逆透视）: <https://zhuanlan.zhihu.com>
- xilejun，《不断靠近数据规范的真谛：从 Excel 到 Tableau》（1NF：单行表头、不合并单元格、有主键）: <https://xilejun.com>

**中文 text2sql 实践（列名/映射讨论）**
- 知乎，《LLM 在中文 Text2SQL 任务上的优化 V2.0》: <https://zhuanlan.zhihu.com/p/673474672>
- AWS ML Blog（中文），使用 Bedrock 和 RAG 构建 Text2SQL 行业数据查询助手: <https://aws.amazon.com/cn/blogs/china/build-text2sql-industry-data-query-assistant-using-bedrock-and-rag>
- OceanBase Blog，Text2SQL 基于元数据（表名/字段名/注释）构建向量化检索: <https://open.oceanbase.com/blog/18028612368>
- Awesome-Text2SQL（eosphoros-ai，DB-GPT 同源社区资源汇总）: <https://github.com/eosphoros-ai/Awesome-Text2SQL>

---

> 注：部分社区/博客来源（Microsoft Support、Alteryx Community、Macrordinary、知乎、xilejun）在检索结果中仅给出站点级 URL，条目内已附检索词以便回查；论文与官方文档均给出可直接访问的深链（撰写时已逐一验证 200）。
