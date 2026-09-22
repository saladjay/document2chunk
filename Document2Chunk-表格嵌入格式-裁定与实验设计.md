# Document2Chunk — 表格嵌入格式：联网裁定与实验设计

**日期**: 2026-09-21
**性质**: 裁定报告 + 实验设计（判据已预登记，见 §5.3）
**状态**: 裁定闭环；bge-m3 臂执行中；qwen3-embedding-8b 臂与真实日志校准挂账 `issues8.md`
**术语**: 表格承载格式 / 嵌入输入格式 / 双表征 chunk，见根目录 `CONTEXT.md`

---

## 0. 结论速览

1. 被核查论断集（外部调研摘要）的文献引用**真实且忠实**：arXiv 2604.24040 确实存在，五条引用性主张全部证实。唯一精度问题：**0.26 是 NQ-Tables 单数据集上的最大极差**（WTQ/WikiSQL 仅 0.09–0.17），「可达」措辞成立但引用时须带边界。
2. 「标签 token 稀释语义权重」**是口传**——多轮定向检索找不到任何定量出处；「HTML vs Markdown 嵌入召回」**公开文献不存在**头对头数字（流传的 60.7% vs 53.6% 是 GPT 表格抽取基准被循环转载，与 embedding 召回错配）。
3. 因此「直接嵌 HTML 还是转换后嵌」**没有任何公开证据可裁决**——本地实验是唯一路径。本文档 §5 即该实验的锁定设计。
4. 本项目命中点：markdown 导出中**含合并格的表格 chunk 现为带标签 HTML**（`export/_helpers.py:113-135` 三分流，DOCX/PDF/OCR 共用），下游原样嵌入向量库；现役 bge-m3 恰是论文四模型中格式间波动最大者（Recall@1 极差 0.264）。

## 1. 缘起与范围

外部调研摘要主张：格式敏感是一阶变量 / HTML 保合并单元格语义 / 原始 HTML 不宜直接嵌入 / 转换后再嵌入更受推荐。经四轮 grill 收窄为本实验：

- **范围落点**：只裁决「嵌入输入格式」（Q4）；「表格承载格式」维持 HTML 现状（结构保真已由规格级证据支撑）。
- **Excel 轨A 不重开**（Q8b）：其嵌入输入是行级 KV 平铺文本，属双轨决策已裁决旧案；仅作实验对照组（2 文件）。
- **推进顺序**（Q1c）：裁定 → 决策 → 实验，判据预登记防「跑完再找理由」。

## 2. 文献裁定：arXiv 2604.24040

**论文**: Bhandari, Singh, Gao, Dan, Gupta. *Improving Robustness of Tabular Retrieval via Representational Stability*. arXiv:2604.24040, 2026-04-27（v2 04-28）。**预印本，未见同行评审**。代码: `github.com/KBhandari11/Centroid-Aligned-Table-Retrieval`（README 内工作标题与 arXiv 正式标题不一致，引用以 arXiv 为准）。

### 2.1 逐条裁定（对原摘要）

| # | 摘要主张 | 裁定 | 论文依据 |
| --- | --- | --- | --- |
| 1 | 2026 年研究量化序列化格式对检索的影响 | ✅ 证实 | §1 贡献一；§2 "The Serialization Bottleneck" |
| 2 | 同一表格不同格式 → 显著不同 embedding | ✅ 证实 | Abstract；§2 Fig.1（同一 WTQ 表的各格式在 ReasonIR 空间中落在不同区域） |
| 3 | Recall@1 波动可达 0.26 | ✅ 证实（带边界） | Table 1 Range 列：MPNet 0.262 / **BGE-M3 0.264（全表最大）** / ReasonIR 0.259 / SPLADE 0.244，均在 NQ-Tables；WTQ 0.095–0.160，WikiSQL 0.092–0.171 |
| 4 | format-specific shift 术语 | ✅ 证实 | §3 核心建模项 δ_s(T)；附录 B/C |
| 5 | HTML 与 CSV/TSV/Markdown/DDL 并列测试 | ✅ 证实 | Abstract 原句；实际全量 **17 种**序列化 |
| 6 | 格式是一阶变量非预处理细节 | ✅ 证实 | §2 小节标题 "First-Order Retrieval Variable"；§6 结论 |

### 2.2 决策关键事实

- **模型**：MPNet / **BGE-M3** / ReasonIR-8B / SPLADE-v3。**qwen3-embedding-8b 未被测**（论文的 8B 是 ReasonIR），其格式敏感性未知，必须实跑。
- **数据集**：WTQ（4,200 问/2,044 表）、WikiSQL（15,878/5,069）、NQ-Tables（966 问/**169,898 表**）。**表级检索，非 chunk 级 RAG**——效应量迁移到 chunk 形态需本地验证。
- **无普适最佳格式**：MPNet 在 WTQ 上 **html 是最差格式**（R@1=0.09；pipe/tsv 最佳 0.25），在 NQ-Tables 上 **html 又是其最佳**。worst 席位常被 shuffled rows 占据；schema 类格式（mschema/ddl）的偏移稳定且与表内容无关。
- **论文方案不可直接照抄**：centroid/adapter 是下游修正，需多格式多遍编码（成本线性增长），且对 SPLADE 类稀疏检索收益有限。
- **引用卫生三条**：① 0.26 有数据集边界；② 摘要式引用隐去了「收益模型相关、稀疏检索有限」的论文自带限定；③ 预印本未评审——**本实验只借其动机，不借其结论当判据**。

## 3. 工程主张裁定

| 主张 | 裁定 | 出处 |
| --- | --- | --- |
| HTML 保 rowspan/colspan；CSV/MD 扁平化丢结构 | ✅ **证实（规格级必然）** | pandoc MANUAL `pipe_tables`：「cells cannot contain block elements…and cannot span multiple lines」；Docling 源码 docstring："Markdown pipe tables have exactly one header row and **no spans**"（`docling_core/transforms/serializer/markdown.py:625`） |
| 有 RAG 项目特意保留 HTML | ✅ 证实（但「提升检索」无定量文献） | RAGFlow issue #16334（HTML 表被拍平成一行，期望如 DeepDoc 以 `<table>` 入 chunk）；LlamaParse `output_tables_as_HTML` 参数 + issue #19602；Unstructured `text_as_html` 一等 enrichment 字段（docs.unstructured.io/concepts/enriching/table-to-html） |
| 标签 token 稀释语义权重 | ⚠️ **传闻**（有说法无定量出处） | 文献中最近表述仅 "extra input tokens and noise"；无任何消融实验支持该机制 |
| HTML→GFM 再嵌入是常见实践 | ✅ 证实但**动机错位** | MarkItDown README「Why Markdown?」：LLM 见过大量 Markdown + token 高效——非标签噪声；Jina Reader-LM 专做 HTML→MD 清洗；Azure AI Search markdown parsingMode |
| HTML vs MD 嵌入召回头对头 | ❌ **公开文献不存在** | 流传的 60.7% vs 53.6% 追溯至 improvingagents.com 的 **GPT 表格抽取基准**（Markdown-KV 60.7 / XML 56.0 / HTML 53.6 / CSV 44.3），被 beam.ai、releasepad.io 等循环转载；测的是「LLM 从格式抽字段」，与 embedding 召回无关；file2markdown.ai 等转轠除外均为利益相关方 |
| 反向：保留 HTML 更好 | ✅ 有一个（但任务不同） | HtmlRAG（arXiv:2411.02959，WWW 2025）：Llama-3.1-70B 上 Markdownify 的 MD 全面低于 HTML（ASQA Hit@1 56.00 vs 58.75）——测的是**检索后喂 LLM**，不回答嵌入问题 |
| 反向：pipe 表缺陷 | ✅ 规格级证实 | 同第一行 |
| 候选线索 Onyx/Danswer 保留 HTML | ❌ **证伪** | main 分支 sparse-clone 后 grep `text_as_html`/`colspan`/`rowspan`/`<table` 零命中 |

**关键工程发现**：Unstructured FAQ 自述主流实践是「**嵌入表格的自然语言摘要**（向量模型为自然语言优化，直接嵌原始表内容效果差），**完整 HTML 只挂 chunk 旁**供渲染/agent 用」——即业界已收敛到「双表征」，与本项目 Q6a 定的 envelope 方向同构（轨A 行级 envelope 已是此形态）。

## 4. 本项目现状命中

| 事实 | 位置 | 含义 |
| --- | --- | --- |
| 导出三分流：合并格表→HTML，简单表→管道表，图表→占位 | `src/document2chunk/export/_helpers.py:75-87,100-135`（designs/009） | 唯一被本议题命中的现状是「合并格表出带标签 HTML」 |
| 本仓库不调 embedding，输出即下游嵌入原文 | `text.md:7`；src 内无 embedding 调用 | 「嵌入输入格式」的决策面 = 本仓库输出格式 |
| Excel 轨A 行级 KV envelope | `extractors/excel/serializer.py:7-25` | 不重开（Q8b），作实验对照 |
| **无召回评测资产** | 全仓无 Recall@k harness（现有仅单元格机械召回 + LLM 评比） | 口径从零建（Q3a），见 §5.2 |
| 可编辑 PDF 路合并信息丢失（全 1×1） | `extractors/_mapping.py:166-205`；designs/008/009 | 已知旧缺口，不在本次范围，注意其表格会走管道表分支 |

## 5. 实验设计（已锁，跨会话以此为准）

### 5.1 变体网格 V1–V6（只改表格段；标题前缀各变体恒定）

| 变体 | 定义 | 示意 |
| --- | --- | --- |
| V1 现状 HTML | `<table>` 带标签原样（基线） | `<table><tr><td colspan="2">华东</td></tr><tr><td>1月</td><td>1024</td></tr></table>` |
| V2 展开管道表 | rowspan/colspan 复制填充为规则网格再转 pipe | `\| 华东 \| 华东 \|` / `\| 1月 \| 1024 \|` |
| V3 KV 平铺 | 轨A 式「表头路径: 值」行级文本 | `华东-1月: 1024; 华东-2月: 2048;` |
| V4 XML | 带行/列坐标属性的规范 XML（**≠论文的列名标签 XML**，见审查4 §4.1② 审核修正） | `<row r="1"><cell c="1" colspan="2">华东</cell></row>` |
| V5 HTML+前置标题 | V1 + 表前一行表题/就近标题 | `【表】分大区月度销售` + V1 |
| V6 NL 摘要进向量 | LLM 生成的表格自然语言摘要（Unstructured 实践） | `该表展示华东大区1-2月销售额，1月为1024元…` |

注：V6 是唯一引入**索引期 LLM 依赖**的变体；若胜出，决策时须权衡该成本。工程核查发现的新实践已并入 V6，网格不再扩（Q13 预授权条款用毕）。

### 5.2 口径

- **Recall@{1,5,10,15,20} + MRR**（Q3a + Q9 用户定步长）；服务 k 后续已知则锁定主指标。
- **真池检索**：样本来源文档全部经现行管线 chunk 化入池（预计数千 chunk），严禁只对被测表检索。

### 5.3 判据（预登记，Q14a）

> 某变体在**双模型**上 Recall@{5,10} 平均提升 ≥3pp，且任一模型劣化 ≤1pp → 切换导出格式并立 ADR。
> 任一模型反向劣化 → 记「格式选择须绑定模型」发现，重议。

bge-m3 臂先行出中间报告（单臂版判据同阈值，标注 qwen 臂待定）。

### 5.4 样本与查询

- 主样本：799 个生产同源 DOCX（`D:\document2chunk-test\docx`）抽 **40 含合并格 + 20 简单表**，跨文档均匀，剔除全空/单列/超长退化表；溯源记录 file:table_id。
- 对照：Excel 轨A 2 文件（Q8b）。
- 查询：每表 4 问（主 160 + 对照 80），**强制混入**同义改写/单位换算/跨行推理，禁止念表格原文；LLM 生成 + 20% 人工抽查（抽查清单输出给用户）；真实查询日志后补走 issues8 校准（Kendall τ 比对）。

### 5.5 模型与运行

- bge-m3（论文被测、现役、本地可跑）先行；qwen3-embedding-8b 等运行条件文档（issues8 B）。
- 选型叠加（Q7c）：矩阵兼作模型对比；两模型结论不一致本身即发现。

### 5.6 执行流程

抽样（T3）→ 变体序列化 + 真池 chunk 化（T4）→ 查询生成 + V6 摘要（T5）→ bge-m3 嵌入与评测（T6）→ 报告与决策（T7）。

### 5.7 有效性风险

| 风险 | 缓解 |
| --- | --- |
| 生成查询词法偏（奖励念原文的变体） | 强制改写配比 + 20% 人工抽查 + issues8 日志校准 |
| 论文效应量是表级检索，chunk 场景未知 | 本实验即为此而设；论文只作动机不作判据 |
| 单一语料域（红头公文） | 生产同源是刻意选择；结论外部效度有限须注明 |

## 6. 引用清单

- 论文：https://arxiv.org/abs/2604.24040 （全文 https://arxiv.org/html/2604.24040v2）
- 代码：https://github.com/KBhandari11/Centroid-Aligned-Table-Retrieval
- RAGFlow #16334：https://github.com/infiniflow/ragflow/issues/16334
- LlamaParse #19602：https://github.com/run-llama/llama_index/issues/19602 ；https://pypi.org/project/llama-parse/
- Unstructured 表格 HTML：https://docs.unstructured.io/concepts/enriching/table-to-html
- Unstructured 博客：https://unstructured.io/blog/preserving-table-structure-for-better-retrieval ；https://unstructured.io/blog/the-case-for-html-as-the-canonical-representation-in-document-ai
- Docling：https://github.com/docling-project/docling-core （`serializer/markdown.py:625`、`serializer/html.py:427-430`）
- MarkItDown：https://github.com/microsoft/markitdown （README「Why Markdown?」）
- Jina Reader-LM：https://jina.ai/reader ；https://jina.ai/blog/reader-lm-small-language-models-for-cleaning-and-converting-html-to-markdown
- Azure AI Search content layout：https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/prep-your-data-for-rag-with-azure-ai-search-content-layout-markdown-parsing--imp/4303538
- pandoc MANUAL pipe_tables：https://github.com/jgm/pandoc （MANUAL.txt）
- HtmlRAG：https://arxiv.org/abs/2411.02959 （WWW 2025）
- GPT 抽取基准（错配溯源）：https://improvingagents.com （60.7/53.6 原始出处，方法学不透明）
- TableRAG/TabRAG：arXiv 2507.12425 / 2511.06582（结构化第三条路）
- Observatory（PVLDB 2023，表 embedding 刻画，非 RAG）：https://github.com/superctj/observatory-library

## 7. 实验结果与判定（2026-09-22 执行完毕）

### 7.1 执行环境

- **嵌入**：112 研发中心模型中转（`:9050`，OpenAI 兼容）。bge-m3（1024 维，=下游 Milvus 生产维度）；qwen3-vl-embedding-8b（4096 维，=§5.5 所称 qwen3-embedding-8b 的中转实际型号）。聊天与嵌入均走研发中心网关上游，不占 AskKnowledge 生产 GPU。
- **生成（双异源，消除同源混杂）**：主查询集 = deepseek-v4（240 问）；V6 摘要 = qwen3.6-35b-a3b（60 段）。教训：思考系模型 max_tokens 给小会只 thinking 不出正文（qwen 摘要实测单次吃 4272 tok，8192 档才稳定）。
- **反抄袭闸**：查询×单元格 LCS≥6 字符剔除 36/240（15%，deepseek 比 session 模型爱抄原文），**主集 204 问、0 表灭**；全量 240 另跑敏感性对照。
- **真池**：10,305 chunk（40 merged+20 simple 来源文档全量 chunk 化；section 7875 / 背景表 2430 / 样本 60）。
- **查询集三份**：主集（clean 204）/ 全量敏感（240）/ 会话集稳健（240，会话模型生成）。

### 7.2 merged 决策面结果（Recall@k / MRR，主集 n=134）

| 变体 | bge-m3 r@5 | bge-m3 r@10 | qwen3-vl r@5 | qwen3-vl r@10 | Δ(bge)@10 | Δ(qwen)@10 |
| --- | --- | --- | --- | --- | --- | --- |
| **V1 现状 HTML** | **0.470** | **0.612** | 0.619 | 0.739 | 基线 | 基线 |
| V2 展开管道表 | 0.396 | 0.590 | 0.604 | 0.694 | −2.2pp | −4.5pp |
| V3 KV 平铺 | 0.358 | 0.500 | 0.604 | 0.701 | −11.2pp | −3.8pp |
| V4 XML | 0.299 | 0.470 | **0.634** | **0.761** | −14.2pp | **+2.2pp** |
| V5 HTML+前置标题 | 0.358 | 0.552 | 0.575 | 0.716 | −6.0pp | −2.3pp |
| V6 NL 摘要 | 0.284 | 0.440 | 0.321 | 0.455 | −17.2pp | −28.4pp |

（r@1/r@20/MRR 及全量数字见 `_embedlab/results/metrics_*.json`，`query_set` 字段核对归属。）

### 7.3 判定（按 §5.3 预登记判据，机械执行）

> **判据不满足 → 维持现状。** markdown 导出中含合并格的表格 chunk 继续**原样嵌出 HTML**（`export/_helpers.py` 三分流不动）。

- 无任何变体达到「双模型 R@{5,10} 平均 +≥3pp」：唯一在单臂正增的 V4（qwen +2.2pp@10）**未过 3pp 线**，且在 bge 上 **−14.2pp 反向劣化**，触发「任一模型劣化 ≤1pp」红线。
- **稳健性三查全部同向**：会话集（bge，n=160）与全量敏感集（bge，n=160）的排序与主集一致（V1 最优或并列最优、V4/V6 显著劣化）；大效应（V4 bge −14pp、V6 双臂 −17/−28pp）远超噪声（n=134 时比例标准误 ≈4pp），小效应（±2–3pp）不做解读。

### 7.4 发现（判据之外的结构性结论）

1. **外部摘要/业界口传的「原始 HTML 不宜直接嵌入」在本语料+本模型上不成立**——注意主张出处：这是调研摘要与 Unstructured 实践的规范性主张，**论文本身未作此主张**；实验与论文在敏感性/无普适最优/模型相关三个核心点上完全一致（审查4 §4.4 审核修正表述）。现状 V1 在 bge 上全指标最优或并列最优。
2. **「格式选择须绑定模型」被本地复现**（预登记备选发现触发）：V4 XML 在 qwen 上最好、在 bge 上倒数——与 arXiv 2604.24040 的模型相关结论同构。若未来选型落到 qwen3-vl-embedding-8b，XML 值得单独重测；换模型必须重跑本实验。
3. **V6「NL 摘要进向量」双臂皆崩**：对以实体/数值/日期为靶的公文表格查询，摘要丢失定位锚点。生成已双异源（查询 deepseek、摘要 qwen3.6），同源措辞混杂不能解释。Unstructured 的「摘要进向量」实践**不迁移**到本场景。
4. **题型拆解**：同义改写型最吃 V1 的词法锚定；「表头语义定位型」在 bge 上 V3 KV 略优（0.70 vs 0.62）——路径键对「某行某列」问法有局部价值，但整体仍不敌 V1。

### 7.5 有效性注记

- 检索为纯稠密向量（生产是 hybrid+rerank）：绝对值不可直接比生产，**变体间差值**是信号。
- qwen3-vl-embedding-8b 是 VL 多模态版，与生产 `:9015` 的 Qwen3-Embedding-8B 非同一权重；结论对「非 VL 的 qwen3-embedding」外推需谨慎。
- 查询为生成集：真实日志校准（issues8 A，Kendall τ）仍是待办；6/240 会话集、36/240 中转集的反抄袭标记已机械剔除。
- 实验执行期间三次后台任务被外部终止、一次指标文件被同名覆盖后重跑修正——最终入库数据以 `results/metrics_{relay,relayall,session}_{bge,qwen}.json` 为准（已逐文件核对 `query_set` 归属）。
- 待办（不阻塞结论）：qwen 臂的会话/全量对照两轮（锦上添花）；人工抽查 20% 查询（清单已出 `gen/spotcheck_relay.md`）。

