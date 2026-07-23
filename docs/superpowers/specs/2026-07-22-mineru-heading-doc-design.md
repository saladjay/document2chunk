# MinerU → 多级标题文档（简化版）设计

> 日期：2026-07-22（2026-07-23 修订：相对定级 + md_content 适配 + 图片提取）
> 分支：`worktree-feat+mineru-heading-doc`（worktree，从 `main` 起）
> 状态：已实现并经真实语料验证（国土资规〔2016〕16号扫描件，`:9030/file_parse`）

## 1. 背景与目标

主仓库 `document2chunk` 已有 PDF（editable）/OCR/DOCX 三路解析 + 统一 `postprocess` 架构。
本设计是一个**独立、简化**的新功能原型，不接入现有架构，专门验证"标题判断能力"。

**功能**：吃 MinerU 的 PDF 分析结果 → 正则补救式标题判定 → 多级标题 + 正文的完整 Markdown 文档。

## 2. 需求决策（已确认）

| 维度 | 决策 |
|---|---|
| 输出形态 | 完整文档（标题 + 正文），标题做骨架，正文/表格/图保留 |
| 与现有架构关系 | 独立包 `mineru2doc/`，不复用 api/SourceType/postprocess；正则内联移植 |
| 标题信号 | MinerU 为主 + 正则补救（修错级 / 补漏检 / 降误检） |
| 实现路径 | content_list/.md + 极简本地模型 + 相对栈式定级 |

## 3. MinerU 服务信息（实测 2026-07-23）

- 主服务 `http://128.23.67.112:9030`（MinerU v3.4.2，vLLM/OpenAI 兼容，内网无鉴权，`/health` 200）。
- 解析端点 `POST /file_parse`，multipart `files=@x.pdf` + `backend=hybrid-engine`。
  - **同步**（~20s）返回 task 对象，结果在 `results[<filename>]`（filename 键可能乱码，取首个 value）。
  - 默认只返回 `md_content`（markdown）；**没有 content_list**。
  - 关键表单参数：`return_images=true`（回图片 base64 dict）、`return_content_list`、
    `response_format_zip`、`effort`、`start_page_id`/`end_page_id` 等（见 `/openapi.json`）。
- ⚠️ 服务巡检文档（`D:\project\server\服务器详细情况.md`）含口令/API Key，**不进 git**。

## 4. 架构与模块布局

独立 top-level 包 `mineru2doc/`，**不 import 任何 `src/document2chunk/`**：

```
mineru2doc/
├── __init__.py       # convert() 端到端入口
├── cli.py            # python -m mineru2doc <input> [-o] [--base-url] [--image-dir] [--demote]
├── model.py          # Block 数据类（type/text/level/bbox/page_idx/number_depth/…）
├── markdown_parser.py# md_content → Block（ATX 标题/HTML 表/图/公式/列表/段落）
├── regex_patterns.py # 编号正则 + section_number_depth（移植自 postprocess/heading_scorer）
├── title_judge.py    # TitleJudge 接缝 + RegexJudge：标 number_depth（相对），不绝对覆盖
├── normalize.py      # 相对栈式定级（深度键栈 + 回上级重置）
├── loader.py         # MinerULoader 接缝：FileLoader / HttpLoader + 图片落盘
├── render.py         # Block[] → Markdown
└── tests/            # 40 单测
```

### 数据流

```
PDF/目录/.json/.md
   │ loader.load(image_out_dir?)      ← HTTP: return_images 取图；File: 拷贝/解析
   ▼
MinerUDoc(List[Block])
   │ title_judge.remediate()          ← 标 number_depth（编号相对深度）+ 补漏检/降误检
   ▼
   │ normalize.normalize_levels()     ← 相对栈式：首见深度落位、更深嵌套、回上级重置
   ▼
   │ render.to_markdown()             ← 标题骨架 + 正文/表/图，图片相对引用
   ▼
完整 Markdown 文档（+ 落盘图片）
```

### 两个接缝

- **`MinerULoader`**：`load(source, *, image_out_dir=None) -> MinerUDoc`。FileLoader 读 content_list.json（富）或 `.md`；HttpLoader POST `/file_parse` 解析 md_content。`image_out_dir` 给定时落盘图片。
- **`TitleJudge`**：`remediate(blocks) -> blocks`。RegexJudge（现在）↔ 未来智能判定器。

## 5. 标题补救规则（核心，2026-07-23 改"相对"）

`title_judge` 逐 block 决策"是不是标题 + 编号相对深度"，**不决定绝对层级**（交给 normalize）。

- **编号块**（① 修错级 / ② 补漏检）：提取编号 depth，标到 `Block.number_depth`。
  - ① MinerU 已判标题 + 有编号 → 标 `number_depth`，**保留 MinerU 层级**（不绝对覆盖）。
  - ② MinerU 判正文 + 有编号 + 可提升（标题部分 ≤ `max_title_len`=40 且无 `[。！？]\S`）→ 标 `number_depth` + 占位 level。
- **无编号 MinerU 标题**：③ 降误检（默认关，`--demote`）：文本 > `demote_min_len`=60 且以句末符结尾 → 降正文。
- 其余：MinerU 无编号标题保留其层级；MinerU 正文无编号保持正文。

> **为什么相对而非绝对**：初版"编号 depth 绝对覆盖 MinerU 层级"（一、=H1）忽略编号方案的起始层级，
> 实测把公文正文一/二/三顶到与文档大标题同级（51 H1 扁平）。改相对后：12 H1 / 30 H2 / 25 H3。

编号→深度映射（`section_number_depth`）：`1`→1、`1.1`→2、`1.2.1`→3；`第一章/篇/部`→1、`第二节/条`→2；
`一、`/`1、`→1；`（一）`/`（1）`→2。

## 6. normalize —— 相对栈式定级

- 维护 `depth_to_level`：首见的编号深度落位到 `prev_level + 1`，同深度复用、更深的嵌套、
  回到浅深度则回到其已落位层级。
- 无编号 MinerU 标题信任其层级（夹防跳级 `min(level, prev+1)`）；当它**回到上级**
  （`lvl < prev`）说明新章节开始 → 清空 `depth_to_level`（编号方案重启）。
- 首个标题恒为 H1（`prev` 从 0 起）。

## 7. render —— Block[] → Markdown

标题 `"#"*min(level,6) + text`；正文原样；table 原样 HTML/markdown；image `![caption](img_path)`；
equation `$$ latex $$`；list `- `/`1. `。可选清理纯页码行（`^\d+(/\d+)?$`，默认开）。
图片保留 md 相对引用 `images/<hash>.jpg`，配合落盘目录就近解析。

## 8. 加载 + 图片提取（参考仓库 OCR `_mapping._image_to_node`）

- **HttpLoader**：`POST /file_parse`（`backend=hybrid-engine`；`return_images=true` 仅当要落盘）。
  从 `results[fn].md_content` 解析 md → Block；从 `results[fn].images`（dict，键=basename，值=base64）
  解码落盘到 `image_out_dir/<img_path>`。
- **FileLoader**：`<name>_content_list.json`（富）或 `*.md`（回退）；`image_out_dir` 给定时从源目录
  拷贝已存在的图片到 `image_out_dir/<img_path>`。
- CLI：`-o out.md` 时图片默认落盘到 out.md 同目录（`--image-dir` 显式指定）。

## 9. 工程化与验证

- **测试**：40 单测（regex / title_judge / normalize / render / markdown_parser / loader / 图片落盘 / e2e）；
  `uv run python -m pytest mineru2doc/tests`（根 `conftest.py` 让顶层包可导入；需 `uv pip install -e ".[dev]"` + `httpx`）。
- **真实语料**：国土资规〔2016〕16号（扫描件）经 `:9030/file_parse` 跑通：标题 12 H1/30 H2/25 H3，
  13 表保留，图片落盘成功。
- **已知小瑕疵**（v1 可接受，后续语料调）：附件落 H2、个别标题带 `\*\*` 转义、
  `**（…）文件` 与"文本格式"同处 H1（均源自 MinerU 自身判定）。

## 10. 演进出口

- `TitleJudge` 接缝：RegexJudge → 多信号/模型判定器。
- `MinerULoader` 接缝：可加 `return_content_list`/`response_format_zip` 走 content_list 富路径（带 bbox/page）。
- 相对定级可加 doc_title offset / 附录 reset（对齐主架构 `calibrate_levels`，按需）。
