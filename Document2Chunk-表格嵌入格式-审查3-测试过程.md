# Document2Chunk-表格嵌入格式-审查包 3 · 测试过程

## 1. 环境

| 项 | 值 | 说明 |
| --- | --- | --- |
| 嵌入/聊天服务 | 112 研发中心模型中转 `http://128.23.67.112:9050/v1`（OpenAI 兼容） | 用户指定（「不要自己重新下载」）；聊天与嵌入走研发中心网关上游，**不占 AskKnowledge 生产 GPU** |
| 鉴权 | key 存服务器 `/opt/llm-relay/config.yaml`，取回后仅落 `_embedlab/.env`，**全程未回显入会话** | |
| 模型 | `bge-m3`（1024 维）、`qwen3-vl-embedding-8b`（4096 维）、`deepseek-v4`（查询生成）、`qwen3.6-35b-a3b`（摘要生成） | [ARMS 常量](_embedlab/embed_eval.py#L27)、[生成模型常量](_embedlab/gen_relay.py#L24) |
| 实验代码环境 | `_embedlab/.venv`（Python 3.12，uv 管理；torch-cpu+sentence-transformers 初装后弃用，实际只需 openai） | 与生产 `.venv` 隔离 |
| 管线解析环境 | 项目 `.venv`（`uv run`），复用现行 [DocxExtractor](src/document2chunk/extractors/docx/extractor.py#L294) | |

## 2. 流水线（八步，命令与产物）

```text
scan_corpus.py → sample.py → build_material.py → gen_requests.py → gen_relay.py
→ merge_gen.py/validate_queries.py → filter_queries.py → warm_cache.py → embed_eval.py
```

| 步 | 命令（cwd=`_embedlab`） | 产物 | 说明 |
| --- | --- | --- | --- |
| 1 全量扫描 | `uv run python _embedlab/scan_corpus.py` | `scan_index.jsonl`（7,282 表/0 崩溃） | 生产管线解析 799 文件 |
| 2 抽样 | `uv run python _embedlab/sample.py` | `sample.json`（40+20，seed=42） | 剔除规则 [ok()](_embedlab/sample.py#L26) |
| 3 材料提取 | `uv run python _embedlab/build_material.py` | `material/*.json` ×57 + `manifest.json` | 块级简化 JSON，lab 侧零项目依赖 |
| 4 请求包 | `uv run python _embedlab/gen_requests.py` | `gen/requests.jsonl` ×60 | 只含网格+标题（无变体信息） |
| 5 异源生成 | `.venv/Scripts/python.exe gen_relay.py` | `gen/queries_relay.json` + `summaries_relay.json` | 3 并发/断点续跑/增量落盘（[main](_embedlab/gen_relay.py#L112)） |
| 6 校验 | `.venv/Scripts/python.exe validate_queries.py gen/queries_relay.json` | `gen/validate_relay.json`（36 标记） | LCS 反抄袭（[lcs](_embedlab/merge_gen.py#L18)） |
| 7 过滤 | `.venv/Scripts/python.exe filter_queries.py` | `gen/queries_relay_clean.json`（204 问） | 剔违规不剔表（0 表灭） |
| 8a 缓存预热 | `.venv/Scripts/python.exe warm_cache.py bge|qwen` | `results/base_emb_*.npy` | 背景池 10,305 chunk 各嵌一次 |
| 8b 评测 | `.venv/Scripts/python.exe embed_eval.py --arm bge|qwen --tag relay --queries gen/queries_relay_clean.json` | `results/metrics_*.json` + `ranks_*.json` | 对照轮换 `--queries` 会话集/全量集 |

会话集生成（对照用）：6 并行 agent 各领 10 表，协议与主集同（产出 `gen/gen_batch1-6.json`，[merge_gen.py](_embedlab/merge_gen.py) 合并校验）。

## 3. 评测器关键正确性设计

- 每 world 只对其覆盖的查询计分（simple 无 V2、V2 世界无 simple）：[idxs 过滤](_embedlab/embed_eval.py#L183)。
- gold 行映射统一 `tbl::` 前缀：[sample_row](_embedlab/embed_eval.py#L181)。
- 背景池嵌入按**模型**缓存（[cache 命名](_embedlab/embed_eval.py#L144)），换查询集复跑不重嵌池。
- 动态字符预算批处理（6000 字符/批，单条截断 6000）：[Embedder.__call__](_embedlab/embed_eval.py#L84)——上游按整批 token 计 8192 上限。

## 4. 判定前后的稳健性措施

1. 三份查询集（主集/全量敏感/会话集）分别评测，排序一致性人工核对（结果见[审查4 §3](Document2Chunk-表格嵌入格式-审查4-结果与机理.md)）。
2. 指标文件以 `--tag` 区分查询集（`relay/relayall/session`），每份 metrics 内含 `query_set` 字段可自证归属。
3. 全部关键产物 md5 摘录于[审查5 §3](Document2Chunk-表格嵌入格式-审查5-源码索引.md)。

## 5. 生成数据可信度

- **双异源**：查询=deepseek-v4、V6 摘要=qwen3.6-35b、稳健对照=会话模型——三个来源两两异家族，「生成器措辞偏袒某变体」缺乏同源通道。
- 生成器输入不含变体信息（只有网格+标题）。
- 思考系模型参数教训：qwen3.6-35b 单次摘要 thinking 实测耗 4,272 tok，`max_tokens=1200/3072` 时正文为空（finish=length），8192 才稳定——这解释了过程日志中早期 s=1 fail=29 的失败段（已修复重跑，断点续跑跳过已完成 key）。
- 失败兜底：JSON 解析容错（剥离 `<think>` + 平衡括号截取，[find_json](_embedlab/gen_relay.py#L62)）、三次重试、增量落盘。

## 6. 事故与修正记录（审核者重点）

| # | 事故 | 影响 | 修正 |
| --- | --- | --- | --- |
| 1 | 本地 bge-m3 下载经 hf-mirror 长时间超时 | 无（未产生数据） | 用户指路中转，弃本地下载 |
| 2 | 上游 8192 token 整批上限致 400 | 预热首轮失败 | 动态字符预算批处理（§3） |
| 3 | 生成脚本 walrus 语法错 + 早期摘要 max_tokens 不足 | 首轮生成 29/30 摘要失败 | 语法修复 + 8192 档重跑；失败段数据未进入任何产物 |
| 4 | 后台任务三次被外部终止（其一经确认误停，其余为健康任务被停） | 断点续跑恢复，无数据损失 | 续跑机制（增量落盘每 10 表） |
| 5 | 评测器 gold 查找 KeyError（`tbl::` 前缀不匹配；先前 idxs 过滤修复世界覆盖问题） | 评测崩溃，无错数入库 | 前缀统一后重跑 |
| 6 | **指标文件同名覆盖**：同臂不同查询集共用 `metrics_{tag}_{arm}.json`，四轮连跑时 bge 主集被会话集覆盖，曾导致一张归属错误的对照表流出 | 短暂（同会话内发现） | 缓存命名改按模型、metrics 按 tag 分离；全部五轮重跑；最终入库文件逐一核对 `query_set` 字段 |

## 7. 数据完整性

- 权威产物：`results/metrics_relay_bge.json`（bge 主集）、`results/metrics_relay_qwen.json`（qwen 主集）、`results/metrics_session_bge.json`（稳健）、`results/metrics_relayall_bge.json`（敏感）——均含 `model/arm/tag/query_set` 自证字段。
- 校验和见[审查5 §3](Document2Chunk-表格嵌入格式-审查5-源码索引.md)。
- 未入库的中间态（覆盖前的旧 metrics、部分生成批次）已被上表机制取代，如需取证 git/回收站不可考——以 §7 权威清单为准。
