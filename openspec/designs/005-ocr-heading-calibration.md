# 设计 005 — OCR 文档级标题定级（heading calibration）

> 状态：已确认（待实现）
> 背景：OCR 按页独立处理，每页 markdown `#`/`##` 是**页内局部**判定，跨页不一致（实测同一级"一、…八、"被随机赋 H1/H2）。
> 决策来源：实测（D:/temp/multipage_test.json）+ 参考 `WebCrawler/{structure.py, analyze_headings.py}` + edited-pdf 的 `BodyAnalysis`/`AutoLevel`/`heading_scorer`。
> 原型验证：编号定级把"一、…八、"全部 → H1（一致）；文档大标题（h=62/74，比值>1.8）→ metadata。

## 问题（实测）

2017.13 号文 7 页，同一级章节 `一、…八、`（均 `cn_major`）的 markdown `#`：
```
p0: ## 一、   p1: # 二、   p2: # 三、 / ## 四、   p3: # 五、 / ## 六、   p4: # 七、   p5: # 八、
```
H1/H2 随机跳。而 `parsing_res_list` 里它们**全是 `title`（无 level）**，bbox 高度全 21-24（一致）→ `#` 的 level 是噪声，**高度 + 编号**才是可靠信号。

## 设计（文档级 pass，OcrExtractor 内、assemble 前）

### 输入候选池（跨所有页）
`{parsing_res_list 的 title/doc_title/paragraph_title 块} ∪ {markdown #-heading}`，每个带 `content / bbox(h, x0) / page`。
- `#` 的 **level 丢弃**（噪声），只取"它是标题"（强信号，防漏）+ bbox。

### 正文基准
所有 `text`/`ParagraphNode` 块 bbox 高度的**众数** = `body_h`（≈22）。

### 逐块定级
1. **编号优先**（主，套 structure.py `_STYLE_LEVEL`，OCR 去 bold 要求）：
   `第X章`/`一、`→H1；`第X节`/`（一）`→H2；`第X条`→H3；`1.`/`(1)`→H4。
2. **无编号 fallback**（辅，参考 edited-pdf 多判据）：`ratio = h / body_h`，按比值阈值聚类成相对层级（H1≥1.6×、H2≥1.3×、H3≥1.15×、H4≥1.05×）+ **x0 缩进**辅助。
3. **文档大标题**（决策1=B）：无编号 且 `ratio > 1.8` → 进 `metadata`，**不进 heading 池**：
   - `metadata.title` = 这些大标题里**文本最长/最具描述性**者（"关于…通知"）。
   - 其余大标题（"XXX文件"版头）→ `metadata.custom["doc_titles"]`（**保留不丢弃**）。
   - content 里这些块降级为 `ParagraphNode`（文本不丢、不污染章节树）。

### 栈 + 覆盖
- `_HeadingStack`（structure.py）：维护路径、同级替换、修层级跳跃（H1→H3 挂到最近 level<3）。
- 用上面定的 level **重写 `HeadingNode.level`**（覆盖噪声 `#`）；H1-9 封顶。

## 已确认决策
1. 文档大标题 → `metadata.title`（不算 heading）；`一、`=H1。✅
2. 无编号 fallback → bbox 高度聚类（参考 edited-pdf 多判据）。✅
3. 丢弃 markdown `#` 的 level，但 `#` 块 bbox 进重定级池（`#`=强"是标题"信号，防漏）。✅
4. 层级 H1-9。✅
5. 多个大标题：`metadata.title`=最长者，其余进 `metadata.custom["doc_titles"]`（不丢弃）。✅

## 实现
- 新增 `src/document2chunk/extractors/ocr/_heading_level.py`：`calibrate(content, metadata) -> list[BlockNode]`（重定级 + 大标题抽 metadata）。
- `OcrExtractor.extract`：各页块汇总后、返回前调 `calibrate`。
- 测试：合成（doc-title + 一-八 带噪声 level）→ 断言 一-八 全 H1、大标题进 metadata；真实文档回归。
