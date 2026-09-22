# Document2Chunk-表格嵌入格式-审查包 5 · 源码索引

> 实验代码全部在 `_embedlab/`（未入库目录，随本包文档固化为审查对象）；生产源码锚点用于说明「现状行为」与「实验如何复刻它」。链接格式 `文件#L行号`，在 GitHub/Gitea 网页端可直接跳转。

## 1. 实验脚本（按流水线顺序）

| 脚本 | 职责 | 关键锚点 | 输入 → 输出 |
| --- | --- | --- | --- |
| [scan_corpus.py](_embedlab/scan_corpus.py) | 799 DOCX 全量扫描建表索引 | [main](_embedlab/scan_corpus.py#L56) · [table_stats](_embedlab/scan_corpus.py#L31) | 语料 → `scan_index.jsonl` |
| [sample.py](_embedlab/sample.py) | 40+20 抽样（seed=42） | [FILTERS](_embedlab/sample.py#L17) · [ok](_embedlab/sample.py#L26) · [pick](_embedlab/sample.py#L37) | 索引 → `sample.json` |
| [build_material.py](_embedlab/build_material.py) | 被抽文档二次提取为块级 JSON | [dump_doc](_embedlab/build_material.py#L83) · [table_dict](_embedlab/build_material.py#L28) | sample → `material/*.json` + `manifest.json` |
| [gen_requests.py](_embedlab/gen_requests.py) | 生成请求包（网格+标题，无变体信息） | [grid_text](_embedlab/gen_requests.py#L17) | material → `gen/requests.jsonl` |
| [gen_relay.py](_embedlab/gen_relay.py) | 中转异源生成（查询+摘要；断点续跑/3 并发/增量落盘） | [Q_PROMPT](_embedlab/gen_relay.py#L30) · [S_PROMPT](_embedlab/gen_relay.py#L47) · [find_json](_embedlab/gen_relay.py#L62) · [gen_with_retry](_embedlab/gen_relay.py#L86) · [main](_embedlab/gen_relay.py#L112) | requests → `gen/queries_relay.json` + `summaries_relay.json` |
| [merge_gen.py](_embedlab/merge_gen.py) | 会话集批次合并校验 + LCS 实现 | [lcs](_embedlab/merge_gen.py#L18) | `gen_batch*.json` → `queries_session.json` |
| [validate_queries.py](_embedlab/validate_queries.py) | 查询集中立校验（键齐/形齐/反抄袭） | [main](_embedlab/validate_queries.py#L18) | queries → `gen/validate_*.json` |
| [filter_queries.py](_embedlab/filter_queries.py) | 反抄袭闸过滤出主集 | [main](_embedlab/filter_queries.py#L19) | queries_relay → `queries_relay_clean.json` |
| [serialize.py](_embedlab/serialize.py) | 六变体序列化器（纯函数） | [expand_grid](_embedlab/serialize.py#L36) · [v1_html](_embedlab/serialize.py#L65) · [v5_caption](_embedlab/serialize.py#L82) · [v2_pipe_expanded](_embedlab/serialize.py#L89) · [_header_keys](_embedlab/serialize.py#L103) · [v3_kv](_embedlab/serialize.py#L130) · [v4_xml](_embedlab/serialize.py#L152) · [serialize](_embedlab/serialize.py#L178) | 表 dict → 文本 |
| [chunking.py](_embedlab/chunking.py) | 真池 chunker（节聚合+表格独立+标题前缀） | [chunk_doc](_embedlab/chunking.py#L27) · [build_pool](_embedlab/chunking.py#L88) | material → `pool_base.json`（10,305） |
| [warm_cache.py](_embedlab/warm_cache.py) | 背景池嵌入缓存预热 | [main](_embedlab/warm_cache.py#L16) | pool → `results/base_emb_*.npy` |
| [embed_eval.py](_embedlab/embed_eval.py) | 评测器（双臂/world 覆盖/指标） | [ARMS](_embedlab/embed_eval.py#L27) · [pipe_plain](_embedlab/embed_eval.py#L34) · [render](_embedlab/embed_eval.py#L52) · [Embedder.__call__](_embedlab/embed_eval.py#L84) · [缓存](_embedlab/embed_eval.py#L144) · [world 覆盖 idxs](_embedlab/embed_eval.py#L183) · [产物写出](_embedlab/embed_eval.py#L215) | pool+queries+summaries → `results/metrics_*.json` |
| [gen_spotcheck.py](_embedlab/gen_spotcheck.py) | 20% 人工抽查清单生成 | [main](_embedlab/gen_spotcheck.py#L15) | queries+validate → `gen/spotcheck_relay.md` |

## 2. 生产源码锚点（现状行为）

| 锚点 | 作用 |
| --- | --- |
| [export/_helpers.py#L75 block_markdown 三分流](src/document2chunk/export/_helpers.py#L75) | **被实验裁决的现状**：挂图→图片；含合并格→HTML；简单表→管道表 |
| [export/_helpers.py#L60 _has_merged_cells](src/document2chunk/export/_helpers.py#L60) | 合并格判定（实验抽样同口径） |
| [export/_helpers.py#L100 table_markdown](src/document2chunk/export/_helpers.py#L100) · [#L113 html_table_markdown](src/document2chunk/export/_helpers.py#L113) | 实验复刻的 V1 两个基线（[pipe_plain](_embedlab/embed_eval.py#L34)/[v1_html](_embedlab/serialize.py#L65)） |
| [extractors/docx/parser.py#L597 _parse_table](src/document2chunk/extractors/docx/parser.py#L597) | gridSpan/vMerge → colspan/rowspan（IR 结构保真来源） |
| [ir/models.py#L143 TableCellNode](src/document2chunk/ir/models.py#L143) | colspan/rowspan 字段定义 |
| [extractors/excel/serializer.py#L7](src/document2chunk/extractors/excel/serializer.py#L7) · [flatten.py#L33](src/document2chunk/extractors/excel/flatten.py#L33) | Excel 轨A KV 文本与表头扁平化（V3 的约定来源） |
| [extractors/_mapping.py#L166](src/document2chunk/extractors/_mapping.py#L166) | 可编辑 PDF 路全 1×1（旧缺口，不在本次范围） |

## 3. 数据产物清单与校验和（md5 前 8 位）

| 产物 | md5(8) | 说明 |
| --- | --- | --- |
| `scan_index.jsonl` | `76522b85` | 7,282 表全量索引 |
| `sample.json` | `d6f01f8c` | 40+20 抽样（seed=42） |
| `manifest.json` | `11ff09c3` | 60 样本表溯源 |
| `pool_base.json` | `e7c83021` | 真池 10,305 chunk |
| `gen/requests.jsonl` | `dbda98a9` | 生成请求包 |
| `gen/queries_relay.json` | `7b1ec4e4` | 主集全量 240 |
| `gen/queries_relay_clean.json` | `89fef4c9` | **主集（判定用）204** |
| `gen/queries_session.json` | `075f6482` | 会话稳健集 240 |
| `gen/summaries_relay.json` | `aca94455` | V6 摘要 ×60 |
| `results/metrics_relay_bge.json` | `6456584f` | **bge 主集判定数据** |
| `results/metrics_relay_qwen.json` | `2d680c2a` | **qwen 主集判定数据** |
| `results/metrics_session_bge.json` | `ffe4ea1c` | 稳健性对照 |
| `results/metrics_relayall_bge.json` | `95fc5957` | 敏感性对照 |

复核方式：`cd _embedlab && md5sum <文件>` 与上表比对；metrics 文件内 `query_set` 字段自证归属（[审查3 §7](Document2Chunk-表格嵌入格式-审查3-测试过程.md)）。

## 4. 完整复跑路径

```bash
cd /d/github/document2chunk/_embedlab
# 全链（约 40 分钟：生成 ~25 分钟、评测 <5 分钟，其余为解析/嵌入）
# 步骤 1-4 需项目 venv（import document2chunk），步骤 5 起用 lab venv
uv run --project .. python scan_corpus.py && uv run --project .. python sample.py
uv run --project .. python build_material.py
uv run --project .. python gen_requests.py
.venv/Scripts/python.exe gen_relay.py
.venv/Scripts/python.exe filter_queries.py
.venv/Scripts/python.exe embed_eval.py --arm bge  --tag relay --queries gen/queries_relay_clean.json
.venv/Scripts/python.exe embed_eval.py --arm qwen --tag relay --queries gen/queries_relay_clean.json
# 对照轮（会话稳健 / 全量敏感）
.venv/Scripts/python.exe embed_eval.py --arm bge --tag session   --queries gen/queries_session.json
.venv/Scripts/python.exe embed_eval.py --arm bge --tag relayall  --queries gen/queries_relay.json
```

依赖：中转可达 + `_embedlab/.env` 的 key；材料/池/嵌入缓存已在仓库内，审计复跑可直接从评测步骤开始（秒级~分钟级）。
