# Document2Chunk — Excel 类文件解析技术调研报告（双轨版）

> 调研日期：2026-09-20（经 /grill-me 多轮评审定稿）
> 目标：为 document2chunk 增加 Excel 类文件（.xlsx / .xls / .et / .csv 等）解析能力，**不依赖 LLM**。
> **双轨定位**（评审中新增的关键需求）：同一套解析底座，服务两类下游——
> **轨 A：chunk2embedding**（行级 JSON，检索分块，先行开发）；**轨 B：text2sql 数据准备**（schema+干净数据，排队二期）。
> 状态：**全部设计决策已定稿**（含 Round 4 难点三档定档，详见 §5.1 与 `Document2Chunk-Excel难点定档表.md`）。

---

## 1. 总体结论

1. **"每行一个 JSON" 是业界已验证的检索友好形态**（RAGFlow General 分块、LlamaIndex `PandasExcelReader(concat_rows=False)` 同款思路），无需发明新范式。
2. **难点全部在结构识别**（表头检测 / 表边界 / 多级表头 / 合并单元格），有成熟启发式算法可循（AAAI 2012、Koci 图模型），纯规则可实现。
3. **不建议引入 Docling / MarkItDown 作为主解析器**：前者弱项恰是核心需求（合并单元格还原差、大表逐 cell 扫描极慢），后者只输出整表 Markdown。
4. **双轨架构：共享前端，双后端**——读取引擎 + 表边界 + 表头检测（结构识别）只写一次，两轨在序列化层分叉。
5. **两轨对值形态的要求结构性相反**（embedding 轨要显示值、SQL 轨要原始值），这是拆成两轨的根本原因，不要试图用一套序列化同时满足。

---

## 2. 双轨架构

```
                     ┌─ 共享前端（一次开发）──────────────────┐
 .xls/.et ─soffice→  │ calamine 读值（快）                    │
 .xlsb ──直接──→     │ + openpyxl 二次读合并/格式（准）        │
 .xlsx/.xlsm ──→     │ → 表边界切分(subtable) → 表头检测       │
 .csv ──轻量路径──→  │   → 多级表头路径扁平化                 │
                     └───────────────┬───────────────────────┘
                        ┌────────────┴────────────┐
                 轨 A: chunk2embedding       轨 B: text2sql 数据准备
                 每行一个 JSON + text         schema(DDL建议) + 干净数据
                 显示值规范化                  原始值+类型推断
                 合计行保留打标                合计行/小计行剔除（双计杀手）
                 （先行开发）                  （排队二期，细节见独立调研子文档）
```

### 2.1 两轨分歧对照表（评审定稿）

| 维度 | 轨 A：chunk2embedding | 轨 B：text2sql |
|---|---|---|
| 值形态 | **显示值为准**：`¥1024.50`、`12%`（货币符/百分号是语义）；千分位去掉、日期 ISO、trim | **原始值+类型**：`1024.5` FLOAT（格式化文本进 SQL 是灾难） |
| 日期 | `2023-12-01`（ISO 文本） | DATE 类型 |
| 分块 | 每行一个 chunk | 不分块，整表入库就绪 |
| 交付物 | 行级 JSON + text（schema 见 §4.1） | DDL 建议 + 类型标注的行数据；不含入库与 NL→SQL 执行 |
| 表头路径 | `-` 扁平化为 JSON 键 | 同左（中文列名直用） |
| 合计行/小计行 | 保留，meta 打标 | **必须剔除**（`SUM()` 双计=答案错误），剔除记 warnings |
| 表身份 | sheet 名进 meta | 表名 = sheet 名（冲突加序号，中文加引号） |
| 隐藏行/列 | 解析 + meta.flag | 可配（默认剔除隐藏列） |
| 值为 null | 不进 text | NULL |

**轨 B 的类型推断**：从列值采样推断 INT/FLOAT/DATE/TEXT；低置信降级 TEXT + warning（不硬猜）。轨 B 数据准备侧的展开调研见独立文档（待后台调研回填）。

---

## 3. 共享前端技术管道

```
格式归一化 → 读取引擎 → 表边界检测(subtable) → 表头检测 → 表头路径扁平化
```

### 3.1 读取引擎层

| 引擎 | 语言/底层 | 速度 | 能拿到什么 | 适用角色 |
|---|---|---|---|---|
| **python-calamine** | Rust (calamine) | ★★★★★（比 openpyxl 快 6~12×） | 值+类型；支持 .xlsx/.xls/.xlsb/.ods | **主读取引擎** |
| **openpyxl** | Python 纯实现 | ★（慢） | **合并单元格 ranges + 字体/填充色等格式** | 二次按需读取 |
| **fastexcel** | Rust calamine 封装 | ★★★★★ | DataFrame | 备选 |
| **DuckDB `read_excel`** | DuckDB 扩展 | ★★★★ | 自带表头探测、可直接 to_json | SQL 流派备选 |
| **LibreOffice headless** | 独立进程 | ★★ | .et/老 .xls → xlsx 归一化 | **复用 docx 链路** |

**两级读取策略**：calamine 读全部值（吞吐保障）+ openpyxl `read_only` 只取 `merged_cells.ranges` 与表头候选行格式（数据量小）。**格式信号（加粗等）只有 openpyxl 有**，calamine 只有值+类型。

性能参考：70 万行级大表，pandas 默认引擎小时级 vs `engine="calamine"` 约 6 秒；Docling Excel 后端逐 cell 扫描有极慢的已知问题（issue #2307）。

### 3.2 表边界检测（一张 sheet 多张表）

| 路线 | 做法 | 覆盖面 |
|---|---|---|
| **空行空列切分**（骨架） | 最大无空行/空列矩形切 contiguous subtable（Excel 排序同款规则） | ~80% 场景 |
| 图/连通域模型 | 非空 cell 建图聚类（Koci `xtable_finder` + DECO 数据集） | 学术最全，工程成本高 |
| 表头锚点定位 | 关键词定位表格起点 | 仅表头可枚举场景 |

修正规则：① 块首列全空→按空列二次切分（并排表）；② 结构一致且仅隔标题行的相邻块考虑合并；③ **以实际扫描值域为准，声明 dimension 只作提示**——真实语料 0 例"声明虚胖"、却有 2 例"声明小于实际"（丢首行/非 A1 起始），风险方向与直觉相反（Round 4 实测修正）。

### 3.3 表头检测启发式

学术基础：Fang et al. AAAI 2012（前向加权平均评分）、Nagy 2016（header extension 多行表头扩展）。

信号清单（按可靠性排序）：
1. **类型同质性**：候选行全字符串、下方数据行类型混杂（最可靠，纯 calamine 值可算）；
2. **格式信号**：加粗/填充色/边框（需 openpyxl）；
3. **形态信号**：行短、无空 cell、处于块首行；
4. **下方延伸判定**：相邻行类型分布相似→并入表头（header extension，解决多行表头）。

### 3.4 多级表头与合并单元格

- 判定表头占 N 行后按行数组处理；合并单元格只有左上角有值 → 表头各层 `ffill()` 还原归属；
- **表头路径扁平化即行级 JSON 键名**：`部门 / 一季度 / 营收` → `华东-子列1` 风格——多级表头问题在行级 JSON 形态下自然消解；
- 数据区合并单元格：值**下放到每一行**（chunk 自包含原则），不加溯源标记（见决策账本 Q4）。

### 3.5 边缘格式政策（评审定稿 Q16）

| 格式 | 政策 |
|---|---|
| `.xls` / `.et` | LibreOffice 归一化为 xlsx 再进管道（复用 docx 老格式链路） |
| **假扩展名**（.xls 改名 .xlsx 等） | **魔数指纹识别 + soffice 兜底**（复用 .doc 链路；真实语料命中率 4%，openpyxl 报 BadZipFile） |
| `.xlsm` | 当 xlsx 读，VBA 宏不碰 |
| **加密/密码保护** | **不做破解**，报明确 error + warning；接口文档写明：打开密码不作访问控制，需下游网关层解密后投递 |
| `.xlsb` | calamine 原生支持直接读 |
| `.csv` | 进 v1（编码嗅探 + 表头检测，复用 90% 管道；无表边界/合并问题） |

### 3.6 sheet 路线分流（Round 4 定稿，源于语料实测）

| sheet 类型 | 检测信号 | 轨 A 处置 | 轨 B 处置 |
|---|---|---|---|
| 数据表（常规） | — | 行级 JSON | 入库表 |
| **文档型**（FAQ/政策汇编，实测 22%） | 表头下数据全为长字符串、无类型对比 | **"表格当文档"**：不建表头，每行/每对转独立文本块（QA 对→"问：…答：…"） | 跳过+warning |
| **表单式**（决算表/签字名单） | 抬头+标题+表头+表中合计混排，行非记录 | **整 sheet 聚合为单个文档块**（key-value 文本） | 跳过+warning |
| 空 sheet（实测 13%） | 0 值 | 跳过+warnings，不崩 | 同左 |

---

## 4. 轨 A 输出 Schema（评审定稿）

### 4.1 顶层结构（方案 A：行平铺）

```jsonc
{
  "file": "xxx.xlsx",
  "warnings": ["Sheet2 含外部引用，值为缓存值"],   // P1 标记集中在文件级
  "rows": [
    { "sheet": "2024营收", "row": 5,
      "data": { "华东-子列1": "¥1024.50", "华东-子列2": "12%" },  // 纯业务键值
      "text": "华东-子列1: ¥1024.50; 华东-子列2: 12%",            // 喂 embedding
      "meta": { "hidden": false, "from_total": false } }          // flag/溯源全在这
  ]
}
```

### 4.2 已定规则（评审定稿）

- **键**：表头路径 `-` 扁平化（`华东-子列1`）；表头含 `-` 的歧义接受（低频）；撞名加`（2）`；空表头生成`列N`（N=物理列号，不丢列）；表头内换行/多余空白压单空格。
- **text**：键值平铺 `; ` 分隔；**值为 null 的键值对不进 text**；不用裸 JSON 字符串当 text（语法字符是 embedding 噪音）。
- **分层原则**：溯源/flag/meta 一律不进 `data`、不进 `text`（减少 embedding 无关字符）。
- **值规范化**：显示值 − 千分位 + ISO 日期 + trim；货币符/百分号保留（语义）；文本去首尾空白。
- **公式/引用**：值为主；单元格级标记（`_formula`/`_ref`/`_external`）放 meta 层；**缓存值缺失时告警并保留 None，不静默填 0**。
- **隐藏**：隐藏 sheet 跳过；隐藏行/列解析 + meta.flag；检索影响留待上线后实验。

---

## 5. 难点全景（22 项，评审确认）

### A 组：结构识别
| # | 难点 | 危害 |
|---|---|---|
| 1 | 多级合并表头（跨行+跨列复合） | 键名错误 |
| 2 | 一 sheet 多表（堆叠/并排/主表+旁注） | 边界切错 |
| 3 | 非 A1 起始、表前标题、表尾合计/备注行 | 表头错位 |
| 4 | 斜线表头、单元格内换行伪表头 | 无法结构化 |
| 5 | 幽灵 used range（格式残留虚胖百万行） | 性能崩溃+空行误判边界 |
| 6 | 隐藏行/列/sheet | 丢数据或收噪音 |

### B 组：联动数据
| # | 难点 | 事实依据 |
|---|---|---|
| 7 | 公式只存缓存值；程序生成文件可能无缓存→None | openpyxl 从不求值 |
| 8 | 跨 sheet 引用联动语义丢失 | 千帆列为 Excel RAG 难点 |
| 9 | 跨工作簿外部引用（calamine 只读缓存，可 #REF!） | 无法刷新 |
| 10 | 名称管理器/结构化引用 | 语义在名称空间 |
| 11 | 数据透视表（值在 pivot cache） | 按 cell 读整块丢 |
| 12 | Power Query/宏（文件内只是结果快照） | 来源不在文件里 |

### C 组：数据质量
| # | 难点 | 典型事故 |
|---|---|---|
| 13 | 文本日期 vs 序列号、前导零丢失、15 位精度截断、科学计数法 | 基因名 SEPT1 当日期 |
| 14 | 空值五义（None/空串/空格/`""`/合并阴影） | 键值歧义 |
| 15 | 重复/空列名、列名跨行 | key 冲突覆盖 |
| 16 | 条件格式当数据（红色=逾期，语义在样式层） | 不读格式丢语义 |
| 17 | 批注/文本框/图形数据 | 不在 cell |

### D 组：工程
| # | 难点 |
|---|---|
| 18 | 大表性能（openpyxl 小时级 vs calamine 秒级） |
| 19 | 格式族谱（.xls/.et/.xlsm/.xlsb/.csv） |
| 20 | 加密文件（政策已定：拒收+文档写明） |
| 21 | WPS 特有兼容 |
| 22 | 超宽表/行列转置表 |

### 5.1 三档定档表【Round 4 已定稿】

处置语汇（Q2 已定）：**P0 正确处理** / **P1 降级标记** / **P2 明确不支持**。定档 = 语料盘点频次 × 两轨正确性权重。

**P0 清点**：轨 A 11 项（合并单元格、表边界、多行表头、dimension 反向、公式缓存、列名冲突、假扩展名、空 sheet、`~$` 锁文件、文档型 sheet、表单式 sheet）；轨 B 10 项（上述去掉文档型/表单式，加类型推断）。
**完整 28 项定档表（含逐项用例映射、合成用例清单、后续优化 backlog、验收标准）见独立文档：`Document2Chunk-Excel难点定档表.md`。**

三个来自真实语料的方向性修正（已并入上文对应章节）：
1. **假扩展名**（.xls 冒充 .xlsx，命中率 4%）→ §3.5 魔数指纹兜底；
2. **dimension 风险方向相反**（"声明小于实际"而非"声明虚胖"）→ §3.2 修正规则 ③；
3. **纯文本 sheet 占 22%**，表头建模不适用 → §3.6 文档型路线。

**交付状态（2026-09-20）**：轨 A 已交付（Qoder 按 `docs/superpowers/plans/2026-09-20-excel-track-a-chunk2embedding.md` 执行，主会话验收）。全量回归 449 passed / 3 skipped / 0 failed；真实语料抽查通过——决算表合计行正确打标（49 行中 1 行 `from_total`）、`基础-最新` 产出三级扁平键（`项目基础信息-基本信息-序号` 形，38 个多级键）、通讯录对照组判 `data_table` 零误判。执行期修复 2 处：① openpyxl `column_dimensions` 键为列字母，需 `column_index_from_string` 转换（计划代码笔误）；② 多级分组表头误判表单式——以"宽合并出现的不同行数"区分（≥2 行=分组→数据表，1 行=横幅→表单式），`_FORM_BANNER_RATIO` 0.8→0.5。**遗留**：假 .xlsx（难点19）因本机无 soffice 未实测，需容器环境复验；`~$` 锁文件守卫测试已补（`test_guard_tilde_lock_file_rejected`）。

---

## 6. 现成框架对比（均非 LLM 路线）

| 框架 | Excel 处理方式 | 契合度 | 备注 |
|---|---|---|---|
| **RAGFlow** | General=逐行 key-value；HTML 模式（>12 行切）；Table=查询期转 ES SQL | ★★★★★ | 工业级同款实现 |
| **LlamaIndex PandasExcelReader** | `concat_rows=False` 每行一 Document | ★★★★ | schema 可抄 |
| **unstructured partition_xlsx** | subtable 切分+标题行识别 | ★★★ | 边界检测可借鉴 |
| **Docling** | openpyxl 逐 cell → 网格 | ★★ | 合并还原差、大表慢 |
| **MarkItDown** | to_html→Markdown 整表 | ★ | 无行级结构 |
| **knowledgestack/excel-parser** | 行级 citation-ready JSON | ★★★★ | schema 参考 |
| **xtable_finder + DECO** | 图模型表格识别 | 研究参考 | 工程成本高 |

---

## 7. 落地建议（双轨版）

1. **共享前端先行**：轨 A 开发即是在打磨底座，轨 B 坐享其成（Q15 已定：轨 A 先行）。
2. **格式归一化复用 docx 链路**（指纹识别 + soffice），运维经验直接复用。
3. **自研而非引入框架**：不可控部分仅表头检测约 200 行启发式，有 AAAI 算法与 RAGFlow 先例。
4. **验收语料**：从 `D:\project\kxx-docs` 盘点选取（每文件测试理由，人工审阅；盘点草稿生成中）。
5. **边缘情况清单**（排期预留）：表前大标题（doc_title 过度提升同款风险，参照 DOCX 阶段 A 教训）、表尾合计/备注行（行类型标记而非丢弃）、隐藏行列（开关+flag）、公式取缓存值、行内多值保持原样、超宽表（行级 JSON 反而最优）、Chartsheet 直接跳过（Docling 崩溃坑）。
6. **轨 B 二期**：数据准备调研已产出（`Document2Chunk-text2sql数据准备调研-draft.md`：类型推断范本、合计行五层识别、中文标识符实测、BIRD 式 schema 产物），评审后立项。

---

## 8. 参考资料

**读取引擎与性能**
- Fastest Way to Read Excel in Python — Haki Benita: <https://hakibenita.com>
- python-calamine (PyPI): <https://pypi.org/project/python-calamine/>
- pandas calamine engine PR: <https://github.com/pandas-dev/pandas/pull/48645>
- DuckDB Excel Extension: <https://duckdb.org/docs/stable/extensions/excel>
- polars.read_excel: <https://docs.pola.rs>

**结构识别（表头/边界/合并单元格）**
- Fang et al., AAAI 2012, Table Header Detection and Classification: <https://ojs.aaai.org>
- Nagy et al. 2016, Table Headers: An Entrance to the Data Mine: <https://digitalcommons.unl.edu>
- Koci, xtable_finder: <https://github.com/elviskoci/xtable_finder>
- pandas 合并表头还原（header=[0,1]+ffill）: <https://gairuo.com>
- Docling 大表性能 issue #2307: <https://github.com/docling-project/docling/issues/2307>

**联动数据/公式**
- openpyxl formula 文档（never evaluates）: <https://openpyxl.readthedocs.io>
- data_only 缓存值机制与 None 陷阱: <https://dev.to>
- calamine docs.rs（缓存值+formula 元数据）: <https://docs.rs/calamine>
- 外部引用缓存机制: <https://www.mrexcel.com>

**框架实现先例 / RAG 场景难点**
- RAGFlow Changelog（Excel 逐行 key-value / HTML 模式）: <https://ragflow.io>
- RAGFlow Table 分块讨论 #6686: <https://github.com/infiniflow/ragflow/issues/6686>
- ragflow-plus 逐行解析思路: <https://blog.csdn.net>
- LlamaIndex PandasExcelReader: <https://docs.llamaindex.ai>
- unstructured partition_xlsx: <https://github.com/Unstructured-IO/unstructured>
- MarkItDown 转换机制讨论: <https://github.com/microsoft/markitdown>
- knowledgestack/excel-parser: <https://github.com/knowledgestack/excel-parser>
- The Hardest Problems in Excel Extraction (Pulse AI): <https://www.runpulse.com>
- 火山引擎：RAG chunk 之 Excel 文档解析: <https://developer.volcengine.com>
- 百度千帆：多 Sheet 页 Excel 解析: <https://qianfan.cloud.baidu.com>
- 面向结构化表格的 RAG 实践（知乎）: <https://zhuanlan.zhihu.com>
