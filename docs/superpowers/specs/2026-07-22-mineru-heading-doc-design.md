# MinerU → 多级标题文档（简化版）设计

> 日期：2026-07-22
> 分支：`feat/mineru-heading-doc`（新 worktree，从 `main` 起）
> 状态：设计已确认，待 spec 评审 → writing-plans

## 1. 背景与目标

主仓库 `document2chunk` 已有 PDF（editable）/OCR/DOCX 三路解析 + 统一 `postprocess` 架构
（见 memory `unified-postprocess-architecture`）。本设计是一个**独立、简化**的新功能原型，
不接入现有架构，专门用来验证"标题判断能力"。

**功能**：吃 MinerU 的 PDF 分析结果 → 用正则做"补救式"标题判定 → 输出一份多级标题 + 正文的完整 Markdown 文档。

**为什么独立**：现有架构的标题判定（`ClassificationStage` / `calibrate_levels`）是为 editable-PDF 的
font 信号设计的，在 OCR/MinerU 这类"无 span 级字号"的输入上会降级且踩过多次坑（doc_title 过度提升等）。
先用一个隔离原型把"MinerU 结果 + 正则补救"跑通，把"标题判定器"做成可替换接缝，为以后接入更智能的
判定能力铺路。

## 2. 需求决策（已与用户确认）

| 维度 | 决策 |
|---|---|
| 输出形态 | **完整文档（标题 + 正文）**，标题做骨架，正文/表格/图保留（类似现有 markdown 导出） |
| 与现有架构关系 | **独立脚本/包，不复用** api/SourceType/postprocess；正则逻辑内联移植，不改原码 |
| 标题信号 | **MinerU 为主 + 正则补救**：信任 MinerU 的标题检出，正则只做修错级 + 补漏检 |
| 实现路径 | **路径 C**：content_list + 极简本地模型 + 轻量栈式定级 |
| 输入 | 先用 MinerU 已知文件格式（`content_list.json`）；HTTP 接口（`:9030/file_parse`）走适配器接缝 |

## 3. MinerU 服务信息

- 主服务：`http://128.23.67.112:9030`（宿主 systemd venv，MinerU2.5-Pro-2605-1.2B，vLLM/OpenAI 兼容）
- 解析端点：`POST /file_parse`，multipart `files=@x.pdf`，`backend=hybrid-engine`
  （pipeline 后端 PDF-Extract-Kit-1.0 未下载，只能 vlm/hybrid-engine）
- `/health` 实测 200；`:9030` 内网服务，无鉴权
- ⚠️ 服务巡检文档（`D:\project\server\服务器详细情况.md`）含口令与 Qwen API Key —— **不进代码、不进 spec 细节、不进 git**。本设计只用端点与调用形状。

## 4. 架构与模块布局

独立 top-level 包 `mineru2doc/`，与 `src/document2chunk/` 物理隔离，**不 import 任何现有代码**：

```
mineru2doc/
├── __init__.py
├── cli.py            # python -m mineru2doc <input> [-o out.md] [--base-url http://...:9030]
├── model.py          # 极简本地 Block：type/text/level/bbox/page_idx
├── loader.py         # MinerULoader 接缝：FileLoader（现在）/ HttpLoader（接 :9030）
├── regex_patterns.py # 内联移植的编号正则 + section_number_depth（从 postprocess/heading_scorer 抄）
├── title_judge.py    # 标题判定器接缝：RegexJudge（现在）↔ 未来智能判定器
├── normalize.py      # 轻量栈式定级（防跳级）
├── render.py         # Block[] → 完整 Markdown 文档
└── tests/            # 单测 + 合成 content_list fixture
```

### 数据流（单向管线，每步独立可测）

```
PDF/目录/.json/URL
   │  loader.load()
   ▼
MinerUDoc = List[Block(type,text,level,bbox,page_idx)] + images
   │  title_judge.remediate()   ← MinerU 为主，正则补救（修错级 + 补漏检 [+ 降误检]）
   ▼
Block[]（标题决策已校正）
   │  normalize.levels()        ← 栈式防跳级
   ▼
Block[]（层级连贯）
   │  render.to_markdown()
   ▼
完整 Markdown 文档（# 标题骨架 + 正文/表/图）
```

### 两个关键接缝

**`MinerULoader`**：`load(source) -> MinerUDoc`
- `FileLoader`（现在能跑）：读 MinerU 标准输出目录的 `<name>_content_list.json`（+ `images/`）；或直接给 `.json`。
- `HttpLoader`（接 :9030）：POST PDF 到 `/file_parse`，把响应归一成 content_list 形态。
- **内部契约 = content_list block 形态**：`{type: text|table|image|equation|list, text, text_level?, page_idx?, bbox?}`，
  `text_level` 存在 = MinerU 判定它是标题（1=`#`）。这是 File/HTTP 两路归一后的稳定形状，HTTP 响应 schema 的变化只影响归一层。

**`TitleJudge`**：`judge(block) -> Optional[int]`（`None`=非标题，`int`=层级）
- `RegexJudge`（现在）：编号正则 + 短文本/无句尾正文启发。
- 未来换更智能的判定器（多信号/模型），只动这一个类。这是"以后判断标题的能力"的替换点。

## 5. 标题补救规则（核心）

`title_judge.remediate()` 逐 block 处理三类决策。MinerU 的 type/text_level 是输入信号，正则只在下列情况干预。

### ① 修错级（MinerU 已判标题，层级给错）

标题文本含编号 → **preliminary level = `section_number_depth(编号)`**，覆盖 MinerU 的 text_level；
无编号 → 保留 MinerU 的 text_level。

编号→层级映射（内联移植自已验证的 `section_number_depth`）：

| 文本 | style | depth |
|---|---|---|
| `第一章 …` / `一、…` | chapter / cn_major | 1 |
| `第二节 …` / `第三条 …` / `（一）…` | section / article / cn_minor | 2 |
| `1 …` | digit | 1 |
| `1.1 …` / `3.2.1 …` | digit | 2 / 3（按点数） |

用"相对 depth"而非 MinerU 绝对层级：MinerU 按字号给 `##`，常把"第二章"给成 level 2；编号 depth 是文档逻辑层级，更可靠。绝对层级全局协调交给 normalize。

### ② 补漏检（MinerU 判正文，但应是标题）

正文 block 满足**全部**条件 → 提升为标题，level = depth：
1. 文本含编号（`extract_section_number` 命中）
2. 去掉编号后的标题部分 `≤ MAX_TITLE_LEN`（默认 **40** 字）
3. 无"句尾后有正文"（`[。！？]\s*\S` 不命中）—— 排除"3.2.1 这是一个完整段落…"这类编号段落

否则保持正文。判据简化自现有 classification 的"纯编号标题 vs 编号+正文混合"，二值化。

### ③ 降误检（MinerU 判标题，显然是正文）— 可选，默认关

标题 block 若**无编号 + 文本 > 60 字 + 以 `。！？` 结尾** → 降为正文。CLI `--demote` 开启。
默认关闭（memory 中 doc_title 过度提升的教训：降级易误伤）。

### 补救效果示例（MinerU → 补救后）

| MinerU 输出 | 补救 | 结果 |
|---|---|---|
| `text_level=2`「第三章 总则」 | 修错级 | `H1`（depth=1） |
| `text_level=1`「（二）适用范围」 | 修错级 | `H2`（depth=2） |
| `text_level=1`「总体要求」 | 无干预 | `H1`（保留 MinerU） |
| 正文「3.2.1 项目查看与处理」 | 补漏检 | `H3` |
| 正文「3.2.1 本模块提供…查看…，并可处理。」 | 不提升（有句尾正文） | 正文 |
| 正文「这是一般正文」 | 无编号 | 正文 |

## 6. normalize.levels() — 轻量栈式

一条规则：遍历 heading，`prev_level` 从 0 起，每个 heading `level = min(level, prev_level + 1)`，更新 `prev_level`。
- 防跳级：`H1 → H3`（缺 H2）收成 `H1 → H2`。
- 首标题归 1：`prev=0` → 首个 heading 恒成 `H1`（doc_title 自然落顶）。
- **不做** doc_title offset、**不做**附录 reset（主架构在这两块踩过坑），列为已知简化项。

## 7. render.to_markdown()

| block | 输出 |
|---|---|
| heading | `"#"*min(level,6) + " " + text` |
| text | 原文 |
| table | MinerU 的 table_body（HTML/markdown）原样 |
| image | `![caption](img_path)` |
| equation | `$$ latex $$` |
| list | `- item` 逐项 |

块间空行分隔。可选预清理：去掉纯页码行（`^\d+(/\d+)?$`），默认开。
输出默认 `<name>.headings.md`，`-o -` 到 stdout。

## 8. 加载接缝

- **FileLoader**：输入目录 → 找 `<name>_content_list.json`（+ `images/`）；或直接 `.json`。逐项 → `Block(type/text/level=text_level/page_idx/bbox)`。
- **HttpLoader**：`POST {base_url}/file_parse` multipart `files=@pdf` + `backend=hybrid-engine` → 响应归一成 content_list 形态 → `Block[]`。响应确切 schema 实现首步用一次真实 curl 样本钉死（内部契约=content_list，只动归一层）。
- CLI 路由：输入是 `.pdf` 且给 `--base-url` → HttpLoader；输入是目录/`.json` → FileLoader。

## 9. 工程化

- **隔离**：新 worktree，分支 `feat/mineru-heading-doc`，从 **`main`** 起（与在飞的 `refactor/unified-postprocess` 解耦）。`mineru2doc/` 不 import 任何 `src/document2chunk/`。spec 批准后用 EnterWorktree 创建。
- **测试**：单测覆盖 regex_patterns / title_judge（用 §5 示例表当用例）/ normalize / render；跑法对齐项目 `uv run pytest`。
- **集成测试样本**：需一份 MinerU `content_list.json`。计划造合成 fixture；并请用户提供一份真实样本做校验。

## 10. 待确认 / 风险

1. **`/file_parse` 响应 schema** —— 实现第一步 curl `:9030/file_parse` 拿真实响应钉死（本机若可连 :9030 且有样本 PDF 可直接探；否则请用户给一份）。
2. **content_list 版本差异** —— Block 是简单 dataclass，忽略未知字段，容差好。
3. **doc_title 与首章同处 H1** —— 已知简化，留待后续。
4. **MAX_TITLE_LEN=40 / 降误检默认关 / 有编号即按 depth 覆盖** —— 已确认，实跑后按真实语料微调。
