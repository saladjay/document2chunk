# Document2Chunk-表格嵌入格式-审查包 2 · 测试内容

## 1. 语料与抽样

- **语料**：`D:\document2chunk-test\docx` 799 个生产同源 DOCX（红头公文，与线上一致）。
- **全量扫描**：799 文件经现行管线解析，索引出 7,282 张表（**3,674 含合并格**），0 崩溃（356s）。扫描器：[scan_corpus.py:56 main](_embedlab/scan_corpus.py#L56)、表统计 [scan_corpus.py:31 table_stats](_embedlab/scan_corpus.py#L31)。合并格判定=任一 cell colspan/rowspan>1，与生产导出三分流的判定函数 [\_has_merged_cells](src/document2chunk/export/_helpers.py#L60) 同口径。
- **抽样**（[sample.py](_embedlab/sample.py)，seed=42 可复现）：退化剔除（挂图路由/非空格<6/单列/行>40/单格>120 字）后 **40 张含合并格（40 个不同文件）+ 20 张简单表（20 个文件）**，共 57 文件 60 表。抽样记录含 file:tbl_idx 溯源（`_embedlab/sample.json`）。
- 被抽文档二次提取为简化块级 JSON（[build_material.py](_embedlab/build_material.py)），tbl_idx 口径与扫描严格一致（[dump_doc](_embedlab/build_material.py#L83)）。

## 2. 六变体（只改表格段；标题前缀恒定）

| 变体 | 定义 | 生产对应 | 实现 |
| --- | --- | --- | --- |
| **V1 现状** | merged=带标签 HTML；simple=管道表 | **即生产三分流行为**：[html_table_markdown](src/document2chunk/export/_helpers.py#L113) / [table_markdown](src/document2chunk/export/_helpers.py#L100) | [serialize.py:65 v1_html](_embedlab/serialize.py#L65) + [embed_eval.py:34 pipe_plain](_embedlab/embed_eval.py#L34) |
| V2 展开管道表 | span 复制填充成规则网格转 pipe | 假想 | [expand_grid](_embedlab/serialize.py#L36) + [v2_pipe_expanded](_embedlab/serialize.py#L89) |
| V3 KV 平铺 | 「表头路径: 值」行级文本（轨A 同约定：空层跳过/层内去重/撞名加（n）） | Excel 轨A [serializer](src/document2chunk/extractors/excel/serializer.py#L7) 风格 | [_header_keys](_embedlab/serialize.py#L103) + [v3_kv](_embedlab/serialize.py#L130) |
| V4 XML | `<row r="i"><cell c="j" colspan.. rowspan..>` 带坐标 | 假想（论文中常胜格式） | [v4_xml](_embedlab/serialize.py#L152) |
| V5 HTML+前置标题 | V1 + 「表格：{表题\|就近标题}」行 | 假想 | [v5_caption](_embedlab/serialize.py#L82) |
| V6 NL 摘要 | LLM 生成的 2-5 句表格摘要进向量（Unstructured 式实践） | 假想 | 生成 prompt [gen_relay.py:47 S_PROMPT](_embedlab/gen_relay.py#L47) |

**公平性要点**：每臂的 V1 = 该表型的生产现状（merged 基线是 HTML、simple 基线是管道表，互不可比也不可比错）；六变体共享同一标题前缀与同一真池，唯一差异是表格段字符串。变体示例与全文见各实现链接。

## 3. 查询集（三份，互为稳健性对照）

| 集 | 生成者 | 规模 | 用途 |
| --- | --- | --- | --- |
| **主集（clean）** | deepseek-v4（112 中转，与摘要生成器异源异家族） | 240 问生成 → **反抄袭闸剔除 36 → 204 问** | 判定依据 |
| 全量敏感 | 同上（含 36 条违禁） | 240 | 敏感性对照 |
| 会话集 | 会话模型（6 并行 agent） | 240（6 条标记未剔除） | 稳健性对照 |

- **生成协议**（[gen_relay.py:30 Q_PROMPT](_embedlab/gen_relay.py#L30)）：每表 4 问，强制四题型——同义改写/数值比较极值/跨行跨列聚合/表头语义定位；答案必须出自该表且问题不得泄露答案；禁抄连续 ≥5 字。生成器只见网格+标题，**不见任何序列化变体**（防变体偏置）。
- **反抄袭闸**：查询×该表全部单元格文本的最长公共子串 ≥6 字符即剔除（[lcs](_embedlab/merge_gen.py#L18)、[filter_queries.py](_embedlab/filter_queries.py)）。剔除 36/240（15%；0 表灭）。校验记录：`_embedlab/gen/validate_relay.json`。
- **V6 摘要**：qwen3.6-35b-a3b 生成（[S_PROMPT](_embedlab/gen_relay.py#L47)），与查询生成器**双异源**——「查询与摘要同源导致 V6 被措辞偏袒」的混杂被结构性排除。
- 人工抽查：`_embedlab/gen/spotcheck_relay.md`（24/60 表，覆盖全部曾有标记的表）——**审核者可直接在此清单上复核**。

## 4. 真池（防「召回100%会骗人」）

- 60 张被抽表的**来源文档全量经现行管线 chunk 化**入池：**10,305 chunk**（section 7,875 + 背景表 2,430 + 样本表 60）。[chunking.py:27 chunk_doc](_embedlab/chunking.py#L27)：非表格按节聚合（~700 字滚动切分）、表格独立成 chunk、chunk=内容+前最近标题行（与真实 RAG 切法同型）。
- 检索时**严禁只对被测表检索**：每条查询要在全池中把自己所属的表 chunk 排进 top-k。池中背景表固定用 V1 形态。
- pool 快照：`_embedlab/pool_base.json`（md5 见[审查5](Document2Chunk-表格嵌入格式-审查5-源码索引.md)）。

## 5. 指标与判定

- **Recall@{1,5,10,15,20} + MRR**（[embed_eval.py:28 KS](_embedlab/embed_eval.py#L28)）；k=10 恰为下游服务取数窗口（easy-rag `MILVUS_KB_DEFAULT_LIMIT=10`）。
- 分臂统计：merged 决策面（n=134，主集）/ simple 对照（对照臂不参与主决策）；四题型拆解。
- **world 覆盖规则**：merged 世界含 V1-V6，simple 世界含 V1/V3/V4/V5/V6（simple 无合并格，V2 展开无意义）——每个 world 只对其覆盖的查询计分（[embed_eval.py:183 idxs](_embedlab/embed_eval.py#L183)）。
- 嵌入：112 研发中心中转 `:9050`（OpenAI 兼容），bge-m3（1024 维=下游 Milvus 生产维度）/ qwen3-vl-embedding-8b（4096 维），余弦相似、动态字符预算批处理（[Embedder.\_\_call\_\_](_embedlab/embed_eval.py#L84)）。
- 判据：见[审查1 §3](Document2Chunk-表格嵌入格式-审查1-目标与问题定义.md)。
