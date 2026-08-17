# 阶段 B 设计：DOCX OOXML 覆盖面补齐

> 日期：2026-08-17
> 输入：`2026-08-17-docx-ooxml-coverage-phase-b-handoff.md`（需求交接）
> 前置状态：阶段 A 未实施（用户决定跳过前置直接开工 B）；本设计基于
> main@f89dbcf 现状，不依赖阶段 A 产物

## 1. 目标与范围

### 1.1 背景

DOCX 解析器只读 `word/document.xml` 的 body 顶层与 run 直接子级，
大量公文常见元素未覆盖。真实样本扫描（799 个公文 docx，几乎全 WPS
产出）显示文本框、OMML 公式、OLE 嵌入、尾注、sdt 容器均有实际出现，
现状在丢内容。

### 1.2 目标

- 按扫描频率（§2）覆盖日出现的 OOXML 特性，解析进统一 IR
- IR 零改动（`FormulaNode`/`InlineFormulaNode`/`ImageNode.data`/
  `metadata.custom` 均已有），纯加性扩展
- 对齐 PDF 路的 `image_dir` 媒体落盘模式

### 1.3 非目标（YAGNI，记录在案）

- 脚注真实使用为 0 → 实现机制但无专属增强（§4.5）
- 修订 `w:ins`/`w:del`（1%）、批注（1%）→ 不做，不在交接 §3 清单
- DOCX 复杂表截图（`table_complex_format="image"`）→ 不接入：
  需 LibreOffice headless 前置转换 docx→PDF，依赖重、CI 不可控，
  收益仅此一种模式；DOCX 复杂表走默认 HTML 渲染（`_has_merged_cells`
  分流 DOCX 已受益）
- EMF/WMF 预览图转 PNG → 不做（渲染需额外依赖）
- 页脚内容进 metadata → 不做（页码为主，无检索价值）

## 2. 真实样本扫描（强制第一步，交接 §3.6）

脚本：`scripts/scan_docx_features.py`（一次性，按局部名匹配规避
WPS 命名空间怪癖）；样本目录 `D:\document2chunk-test\docx`，
共 **799 个 .docx**，生成器几乎全为 WPS Office（各版本）。

### 2.1 频率表

| 特性 | 出现文件数 | 占比 | 总次数 | 结论 |
|---|---|---|---|---|
| 表格 tbl | 658/799 | 82% | 7834 | 已覆盖，回归即可 |
| 页脚 part | 460/799 | 58% | 1530 | 默认不进正文，锁测试 |
| DrawingML w:drawing | 383/799 | 48% | 20343 | 图片已提；补落盘 |
| 媒体位图 | 375/799 | 47% | 15876 | 同上 |
| 页眉 part | 350/799 | 44% | 1134 | 不进正文 + metadata.custom |
| footnotes.xml 存在 | 320/799 | 40% | — | **全为空壳**（仅 separator） |
| endnotes.xml 存在 | 319/799 | 40% | — | 尾注有真实使用 |
| 内容控件 sdt | 219/799 | 27% | 448 | **抽查全为 TOC 容器** → 展开 |
| 页眉含文本(≥10字) | 211/799 | 26% | 20854字 | metadata.custom 增强 |
| 媒体 EMF/WMF | 187/799 | 23% | 7248 | OLE 预览图为主 |
| OLE w:object | 142/799 | 18% | 6151 | 占位 ImageNode |
| VML 图形 w:pict | 126/799 | 16% | 2233 | 文本框 VML 形态 |
| AlternateContent 双写 | 94/799 | 12% | 1020 | 去重总闸 |
| 文本框 txbxContent | 75/799 | 9% | 2623 | **内容补全最优先** |
| OMML oMath(含Para) | 73/799 | 9% | 3892 | FormulaNode |
| 修订 ins / del | 10 / 7 | ~1% | — | 不做（§1.3） |
| 批注 | 8/799 | 1% | — | 不做（§1.3） |
| 尾注引用 enRef | 7/799 | 1% | 256 | **真实参考文献**（单文件最多 61 处） |
| 页眉内文本框 | 5/799 | 1% | 15 | 随页眉不进正文 |
| 脚注引用 fnRef | **0/799** | 0% | 0 | 机制顺带实现，零专属投入 |
| 脚注part真实条目 | 0/799 | 0% | 0 | 同上 |

### 2.2 抽查发现（`scripts/probe_sdt_txbx.py`）

- **sdt 全为 TOC 容器**（`docPartGallery="Table of Contents"`）：
  展开 `sdtContent` 后自然走既有 TOC 域消费路径，无新语义
- **文本框双写成对出现**：`mc:Choice`(wps) + `mc:Fallback`(VML)
  各含一份 `txbxContent`，必须去重否则文字双份；也有无 AltContent
  包裹的纯 DrawingML 文本框
- **OLE 结构**：`w:object` = `v:shape`（含 `v:imagedata` 预览图，
  r:id → media，多为 EMF/WMF）+ `o:OLEObject`（ProgID 如
  Excel.Sheet）；单文件可达 25 个（嵌入表格批量）
- **footnotes.xml 空壳**：40% 文件存在但子节点仅 separator/
  continuationSeparator，正文零引用；尾注才是真实使用（参考文献）

## 3. 架构与数据流

```
.docx (zip)
 └─ PackageReader（扩）
     ├─ 既有: document/styles/numbering/core + media_for_rel
     ├─ 新增: endnotes/footnotes part 读取、header part 文本量、
     │        rel 解析泛化（按 part 名查 rel，不只 document.xml.rels）
     └─ 新增: media 落盘接口（image_dir 模式）

DocumentParser.parse()（body 循环改造）
 ├─ 遍历统一走 embedded.iter_content(el)：遇 mc:AlternateContent
 │   只走 mc:Choice（弃 VML Fallback）——防双计总闸
 ├─ body 顶层遇 w:sdt → 展开 sdtContent 递归当 body 处理（深度上限 10）
 ├─ run 级嵌入物 → embedded.py 分派（parser 只加分派点）：
 │   ├─ m:oMath      → InlineFormulaNode（极简 OMML→LaTeX）
 │   ├─ m:oMathPara  → 块级 FormulaNode
 │   ├─ w:object(OLE)→ 占位 ImageNode（imagedata 预览 + ProgID alt）
 │   └─ 文本框 txbxContent → 锚点段落处内联展开（复用段落分类）
 ├─ 尾注/脚注引用 → 正文保留 [尾注N] 标记 + notes.py 内容块
 └─ 图片: 修 p.iter(blip) 递归泄漏（文本框内图片随锚点展开）

extractor.extract()（扩）
 ├─ 新选项 image_dir: 落盘 word/media/*（沿用 zip 内原名，IR 不引磁盘路径）
 ├─ 页眉非空文本 → metadata.custom["docx"]["header_text"]（截断 200 字）
 └─ 页眉页脚内容默认不进 content（现状等价，锁测试）

IR models.py: 零改动
```

**模块边界**（方案一：分层扩展，用户已选）：

- `embedded.py`（新）：`iter_content` 去重遍历 + 文本框/公式/OLE
  嵌入物解析。输入 lxml 元素 + reader，输出 IR 节点，可独立单测
- `notes.py`（新）：尾注/脚注 part → 内容块 + 引用标记
- `parser.py`：只加分派点（约 +60 行）
- `package_reader.py`：part 读取扩展
- `extractor.py`：image_dir 选项 + metadata.custom + 组装

## 4. 特性设计

### 4.1 AlternateContent 去重（总闸）

`iter_content(root)` 生成器：遇 `mc:AlternateContent` 只走
`mc:Choice`（无 Choice 或不可解析再走 `mc:Fallback`）。图片提取、
文本框发现、**run 文本拼接**统一走它——Choice/Fallback 双写时文字
与图片都只出一份。

### 4.2 文本框（内容补全最优先）

- 发现：run 内 `w:drawing//wps:txbx/txbxContent` 与
  `w:pict//v:textbox/txbxContent` 两形态（去重后只剩一份）
- 展开：`txbxContent` 内是完整 `w:p`（可含 `w:tbl`）列表 → 复用既有
  段落/表格分类，**内联插在锚点段落之后**；红头锚点在文档首部，
  天然归位
- 顺手修 `_extract_images` 的 `p.iter(blip)` 递归泄漏：文本框内
  图片随展开逻辑处理，不再泄出成顶层块
- 展开块带 `metadata={"textbox": true}`，下游可识别红头候选

### 4.3 OMML 公式（极简映射 + 纯文本兜底，用户已选）

- `m:oMath`（run 级）→ `InlineFormulaNode`；`m:oMathPara` → 块级
  `FormulaNode`
- 映射集：`m:f`→`\frac{}{}`、`m:sSup`/`m:sSub`→上下标、
  `m:rad`→`\sqrt`、`m:d`→输出配对圆括号 `(…)`；其余 `m:t` 文本拼接
- 未知结构降级：`FormulaNode(text=纯文本拼接)`，不 fail

### 4.4 OLE 嵌入

`w:object` → `v:shape/v:imagedata`（预览图 r:id → media）+
`o:OLEObject`（ProgID）。产出占位 `ImageNode`：

- `image_id` = imagedata 的 r:id；`format` 取 rel 扩展名
  （emf/wmf 如实记录）；`alt = f"OLE 对象 ({ProgID})"`
- 预览图 rel 缺失 → 仍出占位（format=None，alt 带 ProgID）

### 4.5 尾注/脚注

- 引用点：run 遇 `w:endnoteReference`/`w:footnoteReference` →
  文本位置插标记 `[尾注N]`/`[脚注N]`（N=w:id）
- 内容：part 内非 separator 条目 → `ParagraphNode +
  metadata={"note": {"type": "endnote", "id": N}}`
- **插入位置（用户已选）：文档末尾集中**——单文件最多 61 处引用，
  贴合公文"参考文献在文末"惯例，不打断正文；多条按 id 数值排序
  （Word/WPS 的 id 即编号，数值序还原引用顺序）
- 脚注引用频率 0：机制相同顺带实现，零专属投入
- part 损坏（recover 后仍空）→ 忽略，正文标记保留但无内容块

### 4.6 sdt 展开

body 遇 `w:sdt` → 取 `sdtContent` 子元素按 body 逻辑处理（TOC
容器自然进既有 `in_toc` 消费路径）；run 级 sdt 同样展开；嵌套深度
上限 10 层，超限截断 + WARN。

### 4.7 图片落盘 image_dir

- `extract(..., image_dir=None)`；提供时**仅落盘被引用的媒体**
  （产出了 ImageNode 的，含 OLE 预览图），按 zip 内原名
  （`image1.png` 等，唯一无歧义）；未引用的媒体不倒出
- `ImageNode.data` 仍默认 `None`（内存不驻留 bytes）
- 落盘失败（磁盘/权限）→ WARN + 跳过，解析不中断（对齐 PDF）
- 命名对齐 PDF 模式 `f"{image_id}.{fmt}"` 的精神但不改
  `image_id=r:embed` 契约（spec §3.4）

### 4.8 页眉页脚

- 默认不进 content：现状 body 本就不含，锁测试断言
- 页眉非空文本 → `metadata.custom["docx"]["header_text"]`
  （拼接非空行，截断 200 字）
- 页眉内文本框（1%）随页眉处理，不进正文

## 5. 错误处理

延续 spec §3.8 降级策略：

- 单个嵌入物（文本框/公式/OLE）解析异常 → WARN + 跳过该嵌入物，
  段落其余内容不受影响
- footnotes/endnotes part 缺失或损坏 → 忽略（引用标记保留）
- OLE 预览图 rel 缺失 → 占位 ImageNode 仍产出
- image_dir 落盘失败 → WARN + 跳过落盘
- sdt/AlternateContent 嵌套超 10 层 → 截断 + WARN

## 6. 测试与验收

### 6.1 单测（手搓 fixture 仿 WPS 写法；真实样本不入库，用户已选）

| 特性 | fixture 要点 | 断言 |
|---|---|---|
| AlternateContent 双写 | Choice(wps)+Fallback(VML) 各放不同文字 | 文字只出一份（取 Choice） |
| 文本框红头（wps） | 首段锚点 drawing→wps:txbx 多段落 | 展开块紧随锚点、`metadata.textbox=true` |
| 文本框（VML） | `w:pict//v:textbox` | 同上 |
| 文本框内图片 | blip 在 txbxContent 内 | 不泄出顶层、随展开块 |
| OMML | oMath(frac/sup/sub)+oMathPara | InlineFormulaNode.latex、FormulaNode 块 |
| OLE | v:shape+imagedata+OLEObject | 占位 ImageNode、alt 含 ProgID |
| 尾注 | 2 条 endnote + 3 处引用 | 正文含 `[尾注N]`、内容块在末尾、metadata.note 正确 |
| 脚注 | 1 条 footnote | 同机制 |
| sdt TOC 容器 | sdt 包 TOC 域 | 走 TOC 消费、不进 content |
| sdt 包正文 | sdtContent 内普通段落 | 段落不丢 |
| 页眉页脚锁 | header1.xml 含文本 | content 无页眉内容、custom 有 header_text |
| image_dir | zip 含 2 媒体 | 落盘 2 文件、原名、IR 无磁盘路径 |

真实样本人工抽查：从 799 个挑 5 个（红头/OLE 表/尾注报告/公式/
文本框各 1）核对 markdown 导出。

### 6.2 验收（对照交接 §6）

1. 频率表进设计文档 ✅（§2.1）
2. 日出现特性全覆盖：文本框/公式/OLE/尾注/sdt/页眉页脚/媒体 ✅（§4）
3. 零频率记录：脚注引用、修订、批注 ✅（§1.3、§2.1）
4. pytest 全绿；PDF/OCR 零改动即无回归
5. `openspec/specs/docx-extractor/spec.md` 同步更新（列入实现计划）

## 7. 与阶段 A 的兼容

- 阶段 A 未实施：本设计不碰 `postprocess.py`、不引入
  `heading_source` 依赖
- heading 产出路径不动（只在 run/body 遍历层加分支），阶段 A 合入
  后无冲突；阶段 A 落地时其 `calibrate_levels` 可消费本设计展开的
  文本框标题块
- 公共底线：`postprocess.py` 为三路共享，本阶段不改它

## 8. Worktree 策略

- worktree：`.claude/worktrees/docx-ooxml-coverage`，
  分支 `worktree-docx-ooxml-coverage`，基点 main@f89dbcf
- 基线：165 passed（numpy 为环境补充依赖，非 pyproject 声明）
- 扫描/探针脚本随设计文档一并提交（`scripts/scan_docx_features.py`、
  `scripts/probe_sdt_txbx.py`），扫描产物 `scan_result.md` 不入库
