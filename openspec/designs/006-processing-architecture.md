# 设计 006 — 处理过程架构（详细）

> 状态：权威架构参考
> 覆盖：edited-PDF 路线（9-Stage pipeline）、OCR 路线（远程服务）、共享后处理、structure.assemble、export/debug
> 关联：designs/001(IR 定义)、003(源码汇总)、005(标题定级)、WebCrawler/structure.py(编号正则参考)

---

## 1. 系统总览

```
                    ┌──────────────┐
                    │  source PDF  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  pdf_detect  │  ── detect_pdf_type() 返回 DetectResult
                    │  editable /  │     editable ≥70% 页 → editable
                    │  scanned /   │     scanned ≥70% → scanned
                    │  mixed       │     否则 → mixed
                    └──┬───────┬───┘
              editable │       │ scanned / mixed
                        │       │
  ╔═════════════════════▼═╗   ╔═▼════════════════════════╗
  ║ EDITED-PDF PATH       ║   ║ OCR PATH                  ║
  ║ (pdf-extractor)       ║   ║ (ocr-extractor)           ║
  ║                       ║   ║                           ║
  ║ PyMuPDF → spans       ║   ║ Per-page chunk (PyMuPDF)  ║
  ║ pdfplumber → tables   ║   ╒═══════════════════════════╕
  ║                       ║   ║ Remote PaddleOCR Service  ║
  ║ ┌───────────────────┐ ║   ║ /api/{pp-ocrv6|vl|unlimit}║
  ║ │ 9-STAGE PIPELINE  │ ║   ║                           ║
  ║ │ (element dict 层) │ ║   ║ Response:                 ║
  ║ │                   │ ║   ║  markdown (GFM-ish)       ║
  ║ │ BodyAnalysis  (G) │ ║   ║  images {id: base64}      ║
  ║ │ ImageDetect   (L) │ ║   ║  layoutParsingResults[]   ║
  ║ │ Classification (L)│ ║   ║    page_index(1-based)    ║
  ║ │ TOCDetection  (L) │ ║   ║    width/height (1000)    ║
  ║ │ LayoutFilter  (L) │ ║   ║    parsing_res_list[]     ║
  ║ │ TOCAnalysis   (G) │ ║   ║      block_label          ║
  ║ │ Merge ─────── (L) │ ║   ║      block_content        ║
  ║ │  │ ratio=1.5     │ ║   ║      block_bbox [x,y,x,y] ║
  ║ │ AutoLevel    (G)  │ ║   ║      block_order          ║
  ║ │ PageNumber   (G)  │ ║   ║                           ║
  ║ └────────┬──────────┘ ║   ║      ↓                    ║
  ║          │            ║   ║ markdown→IR mapping:      ║
  ║ element→BlockNode     ║   ║  parse_markdown()         ║
  ║ mapping               ║   ║  build_page_blocks()      ║
  ║                       ║   ║  (ATX标题/HTML表格/图片/  ║
  ║                       ║   ║   列表/公式→BlockNode)    ║
  ║                       ║   ║  bbox 校准 (1000→页坐标)  ║
  ╚═══════════╤═══════════╝   ╚═══════════╤═══════════════╝
              │                           │
              │ raw BlockNode list        │ raw BlockNode list
              │ (per-page, AutoLevel级)   │ (per-page, markdown #级)
              │                           │
              └───────────┬───────────────┘
                          │
  ╔═══════════════════════▼═══════════════════════════════╗
  ║         SHARED POST-PROCESSING (heading.py)           ║
  ║         文档级 · 操作 BlockNode · 两路共用              ║
  ║                                                       ║
  ║  ① calibrate(content, metadata, use_height_fallback)  ║
  ║     ┌─ 编号正则 → 固定层级                             ║
  ║     │   一、/第X章 → H1                               ║
  ║     │   （一）/第X节 → H2                              ║
  ║     │   第X条 → H3                                    ║
  ║     │   1./(1) → H4                                   ║
  ║     ├─ 无编号 fallback:                               ║
  ║     │   OCR: bbox高度/body_h 比值聚类                 ║
  ║     │        H1≥1.6× H2≥1.3× H3≥1.15× H4≥1.05×       ║
  ║     │   edited: 保留 AutoLevel 原级                   ║
  ║     ├─ 大标题(no#, ratio>1.8) → metadata.title        ║
  ║     │   其余大标题 → metadata.custom["doc_titles"]     ║
  ║     ├─ 附表/附件/附录 → prev_level=0 (层级重置)        ║
  ║     ├─ 栈式单调 (lvl > prev+1 → clamp)                ║
  ║     └─ _merge_headings()                              ║
  ║         相邻同level无编号 + 首段无句号 → 合并           ║
  ║                                                       ║
  ║  ② join_cross_page_paragraphs(content)                ║
  ║     page N末段(无句号结尾) + N+1首段 → 拼接            ║
  ║                                                       ║
  ║  ③ filter_cross_page_noise(content, strip_ratio=0.10) ║
  ║     文本在≥2页顶/底strip区重复 → 移除(页眉/页脚)       ║
  ╚═══════════════════════╤═══════════════════════════════╝
                          │
                          │ refined BlockNode list
                          │
               ┌──────────▼──────────┐
               │ structure.assemble  │
               │ (章节树栈算法)       │
               │ HeadingNode(level)   │
               │  → SectionNode 树    │
               │  + block_to_section  │
               │  + 可选 TocNode      │
               └──────────┬──────────┘
                          │
                          ▼
                 ┌────────────────┐
                 │ LogicalDocument │
                 │ (IR)            │
                 └───────┬────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
          export     debug/viz    下游 RAG
        (MD/JSON)   (bbox叠加)   (切片/向量)
```

---

## 2. edited-PDF 路线（pdf-extractor + 9-Stage pipeline）

### 2.1 输入处理

| 步骤 | 工具 | 产出 |
|---|---|---|
| 文本提取 | PyMuPDF `get_text("dict")` | spans: text/font/size/bbox/origin/flags |
| 表格提取 | pdfplumber `find_tables()` 优先 + PyMuPDF `find_tables()` 兜底 | table grids |
| 图片提取 | PyMuPDF `extract_image()` / `get_image_info()` | image bytes + bbox |
| 排序 | (y_top, x0) 主排 + bubble swap 相邻列 | order_index |
| 文表去重 | `_bbox_overlap > 50%` → 排除文本 | element dict list |

element dict 字段：
```python
{
  "type": None,             # 由 Classification 填充
  "label": "text_line",
  "level": None,            # heading level (1-9)
  "text": "合并行文本",
  "markdown": "= text",
  "bbox": [x0, y0, x1, y1],
  "order_index": 0,
  "style": {"font": "SimSun", "size": 12.0, "bold": False, "italic": False, "flags": 0},
  "spans": [{"text": "...", "font": "...", "size": 12.0, "bbox": [...], "origin": [...], "flags": 0}],
  "heading_confidence": 0.0,          # 由 Classification/AutoLevel 填
  "heading_level_conf_history": [],   # 评分历史
  "page_index": 0
}
```

### 2.2 9-Stage Pipeline 详解

**执行模型**：G/L 分组——连续相同 `is_global` 的 Stage 合段。Local 段逐页跑，Global 段合并所有页一次跑。`page_offsets` 切分（假设 Global Stage 不改元素数量）。

| # | Stage | G/L | 读 | 写/副作用 | 核心逻辑 | 关键阈值 |
|---|---|---|---|---|---|---|
| 1 | **BodyAnalysis** | G | spans.font/size | ctx.body_font, ctx.body_font_size | 按 (font, normalize_font_size) 统计字符数→取众数 | normalize 步长 0.2pt；空兜底 Unknown/12.0 |
| 2 | **ImageDetection** | L | ctx.image_infos, elem.bbox | elem.type="image" 占位 | 中心点落入 image bbox 或交叠>50% → 图片占位 | 交叠/元素面积 >0.5；无匹配占位 order=9999 |
| 3 | **Classification** | L | ctx.body_*, elem.style | elem.type/level/heading_confidence/history | size > body×1.15 → heading候选; infer_heading_level_with_score | H1≥1.6× 0.50分, H2≥1.3× 0.45, H3≥1.15× 0.40, H4≥1.05× 0.30；容差 0.5pt |
| 4 | **TOCDetection** | L | elem.text, bbox | elem.type="toc_entry"/"toc_title" | ≥3连续点线 → 目录页；点线正则 `\.{3,}`/`…`/`···` | min_run=3；_DOT_LEADER_RE；_TOC_TITLE_KEYWORDS={目录,...} |
| 5 | **LayoutFilter** | L | ctx.layout_data, page_* | **移除**元素 | PaddleOCR版面框(136→72 DPI) + 启发式8%页眉页脚 → 中心点落入非正文框即丢 | LAYOUT_DPI=136/PDF_DPI=72；框扩展 5%；strip 8% |
| 6 | **TOCAnalysis** | G | toc_entry, 正文 paragraph | 正文→heading+level | depth_ratio≥0.5 → depth建map else 缩进建map；正文匹配→赋级 | exact 0.70/prefix 0.60/cleaned 0.55；跳过 conf≥0.50 |
| 7 | **Merge** | L | elem.type/level/style/bbox | 合并 text/bbox/spans | 段落:同level+字号差≤0.5pt+同字体+**间距≤行距×1.5**; 标题:同行 y_diff≤5 | **_PARAGRAPH_BREAK_SPACING_RATIO=1.5**(Phase 2D: 原1.8→1.5) |
| 8 | **AutoLevel** | G | ctx.body_*, elem.flags/bbox/text | elem.level, ctx.max_heading_level | 独立行+章节号(+0.35)/bold(+0.30)/字号略大(+0.25)/字体不同(+0.20)/大间距(+0.15) → conf≥0.50赋级 | bold位 0x10；大间距 1.5×均值 |
| 9 | **PageNumber** | G | elem.bbox/text/page_index | elem.type="page_number" | 底部元素匹配正则；≥70%页面命中才保留 | 5条正则；DOCUMENT_RATIO=0.7 |

**SplitPipeline 分流**：
- Phase 1: BodyAnalysis(G)
- Phase 2: ImageDetection + Classification + TOCDetection(L)
- Phase 3: 分流（type ∈ {toc_entry,toc_title} → 目录页）
- Phase 4: 目录页走 LayoutFilter + PageNumberDetection
- Phase 5: 正文页走 LayoutFilter → TOCAnalysis(全页跑取正文) → Merge → AutoLevel → PageNumberDetection
- saved_body 保存/恢复 ctx.body_font/size（Phase 3 前快照）

### 2.3 element → BlockNode 映射

| element type | → BlockNode |
|---|---|
| title(H1) / heading(H2-9) | HeadingNode(level, text, runs) |
| paragraph | ParagraphNode(runs, text) |
| table | TableNode(rows) |
| list | ListNode |
| image | ImageNode |
| toc_entry / toc_title | （信号，不进 content；keep_toc 时聚 TocNode） |
| page_number | （丢弃） |

span → RunNode：`text/style(font,size,bold,italic)/provenance(page_index,bbox)`

### 2.4 已知 bug（已修）

- `_redistribute` 读 `_page_index` 但机器注入 `page_index` → 修正键名
- `_stage_counter` 手动接力 → 共享 tracer
- `saved_body` 补丁 → 正确传递 ctx
- SplitPipeline 延迟 import → 构造注入 stage 列表

---

## 3. OCR 路线（ocr-extractor + 远程服务）

### 3.1 输入处理

```
PDF → PyMuPDF 按页切(iter_pages) → 每页 1 页 PDF 子集
                                     │
                    ┌────────────────┘
                    ▼
           OcrServiceClient.parse(page_bytes, model)
                    │  httpx POST /api/<model>
                    │  3次指数退避重试(超时/5xx)
                    │  Authorization: Bearer <token>
                    ▼
           Response JSON
```

### 3.2 服务响应 schema（实测）

```jsonc
{
  "markdown": "<整本 GFM-ish markdown>",
  "images": { "<filename>": "<base64_png>" },
  "layoutParsingResults": [{
    "page_index": 1,              // 1-based
    "width": 1000, "height": 1000,  // 归一化空间
    "markdown": { "text": "<每页md>", "images": {...} },
    "parsing_res_list": [{
      "block_label": "title|text|table|image|image_caption|equation|page_number|header|footer",
      "block_order": 0,
      "block_content": "<文本或HTML>",
      "block_bbox": [x1, y1, x2, y2]   // 1000空间
    }]
  }]
}
```

### 3.3 markdown 方言（实测）

| 元素 | 格式 | → IR |
|---|---|---|
| 标题 | ATX `#`..`######` | HeadingNode（**level 会被后处理覆盖**） |
| 表格 | HTML `<table><tr><td>` | TableNode(lxml 解析, colspan/rowspan) |
| 图片 | `![alt](ocr_images/..png)` | ImageNode(images[ref] base64→落盘) |
| 列表 | `- `(无序) / `1)`(有序) | ListNode |
| 块公式 | `\[..\]` | FormulaNode(latex) |
| 行内公式 | `\(..\)` | InlineFormulaNode(F18, latex) |
| 段落 | 纯文本 | ParagraphNode(runs: RunNode/InlineFormulaNode 交替) |

### 3.4 bbox 坐标校准（designs/005 前置）

OCR 服务的 bbox 在 **1000×1000 归一化空间**（x/y 各自归一化），需换算到**源自然坐标系**：
- PDF 源：`bbox_pt = bbox_1000 / 1000 × page_width_pt`（PyMuPDF page.rect）
- 图片源：`bbox_px = bbox_1000 / 1000 × image_pixel_dim`（PIL.size）

由 `_convert_bbox(bbox, page_w, page_h, service_w, service_h)` 在 `build_page_blocks` 中完成。

### 3.5 模型选择（不写死）

| 场景 | 模型 | 端点 |
|---|---|---|
| 长文档(>20页) | Unlimited-OCR | `/api/unlimited-ocr` |
| 复杂版式 | PaddleOCR-VL 1.6 | `/api/paddleocr-vl-1.6` |
| 规整公文 | PP-OCRv6 | `/api/pp-ocrv6` |

- 默认用 active 模型（GET /api/model-runtime）
- `options.ocr_model` 可显式覆盖
- **不写死选择策略**（澄清2 A1/A2：先建统一层，后置研究）
- **按页送**（49页整本 500 → 逐页；每页 ~9s）

### 3.6 配置（env）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `DOCUMENT2CHUNK_OCR_ENDPOINT` | `http://128.23.67.112:8000` | 服务地址 |
| `DOCUMENT2CHUNK_OCR_TOKEN` | — | Bearer token（**禁止入库**） |
| `DOCUMENT2CHUNK_OCR_MODEL` | `vl` | 默认模型 |
| `DOCUMENT2CHUNK_OCR_TIMEOUT` | `180` | 请求超时(s) |
| `DOCUMENT2CHUNK_OCR_MAX_RETRIES` | `3` | 重试次数 |

支持 `.env` 自动加载（`_config.py _load_dotenv`，零依赖）。

---

## 4. 共享后处理层（heading.py）

> **位置**：两路 extractor 产出 raw BlockNode list 后、`structure.assemble` 前。
> **操作对象**：BlockNode（IR 节点），**不是** pipeline element dict。
> **作用域**：**文档级**（需要所有页的块在一起）。
> **为什么不做成 pipeline stage**：pipeline stage 操作 element dict（pre-IR, font/size/bbox/span 字段），按页跑；后处理操作 BlockNode（post-IR, provenance/level/runs 字段），文档级。两个抽象层，不可混。

### 4.1 ① calibrate(content, metadata, *, use_height_fallback)

**职责**：跨页统一标题层级 + 多行标题合并 + 大标题抽 metadata + 附页重置。

**流程**：

```
输入: content(List[BlockNode]), metadata(DocumentMetadata)
      use_height_fallback: True=OCR / False=edited-PDF

Step 1: 计算正文基准高度
  body_h = mode(ParagraphNode.bbox 高度)    ← 仅 OCR 模式用

Step 2: 遍历 HeadingNode,逐个定级
  ┌─ 附表/附件/附录? → prev_level=0, level=1 (新子文档)     ─┐
  │                                                          │
  ├─ 编号正则匹配?                                           │
  │   一、/第X章 → level=1                                   │
  │   （一）/第X节 → level=2                                 │
  │   第X条 → level=3                                        │
  │   1./(1) → level=4                                       │
  │                                                          │
  ├─ 无编号 + OCR(use_height_fallback=True):                │
  │   ratio = bbox_h / body_h                               │
  │   ratio ≥ 1.8 → 大标题 → metadata (降级为 ParagraphNode) │
  │   ratio ≥ 1.6 → H1                                      │
  │   ratio ≥ 1.3 → H2                                      │
  │   ratio ≥ 1.15 → H3                                     │
  │   ratio ≥ 1.05 → H4                                     │
  │   else → H5                                              │
  │                                                          │
  ├─ 无编号 + edited(use_height_fallback=False):            │
  │   保留 AutoLevel 原级 (b.level 不变)                     │
  │                                                          │
  └─ 栈式单调: lvl > prev_level+1 → clamp 到 prev_level+1   ─┘

Step 3: 多行标题合并 (_merge_headings)
  遍历相邻 HeadingNode:
    同 level + 都无编号 + 首段无句号结尾(。！？.!?) → 合并文本
    最多连续合并 4 个片段
  示例: "广东省…关于印发《广东省承接" + "实施细则》的通知" → 一个标题

Step 4: 大标题 → metadata (仅 OCR)
  doc_titles.sort(key=len, reverse=True)
  metadata.title = doc_titles[0]               (最长=真标题)
  metadata.custom["doc_titles"] = doc_titles[1:] (版头等,不丢)

输出: refined content list
```

**编号正则**（参考 WebCrawler structure.py `_STYLE_LEVEL`）：

| 正则 | style | level |
|---|---|---|
| `^第[一二三...]+章` | chapter | 1 |
| `^[一二三...]+、` | cn_major | 1 |
| `^第[一二三...]+节` | section | 2 |
| `^[（(][一二三...]+[）)]` | cn_minor | 2 |
| `^第[一二三...]+条` | article | 3 |
| `^(\d+[.、]\|[(（]\d+[)）])` | digit | 4 |
| `^(附[表录件]\|附录)` | appendix | 重置为 1 |

### 4.2 ② join_cross_page_paragraphs(content)

**职责**：跨页段落续接。

```
遍历相邻 ParagraphNode 对 (b1, b2):
  条件:
    1. b2.page_index > b1.page_index (跨页)
    2. b1.text 最后一个字符 ∉ {。！？.!?；;:：\n\r} (未完结)
  → 合并: b1.text += b2.text; b1.runs += b2.runs; 移除 b2
```

典型场景："…因成片开发征"(pN末) + "收土地的，不再…"(pN+1首) → 合并。

### 4.3 ③ filter_cross_page_noise(content, strip_ratio=0.10, min_repeat=2)

**职责**：跨页页眉/页脚精滤（补 LayoutFilter 的固定百分比）。

```
Step 1: 估算各页高度
  page_max_y[page_index] = max(所有该页 BlockNode 的 bbox[3])

Step 2: 收集 strip 区文本
  对每个 BlockNode:
    y 在 top strip_ratio 或 bottom (1-strip_ratio) 区内 → 记录 text

Step 3: 跨页重复 → 噪声
  text 出现 ≥ min_repeat 次 → noise 集

Step 4: 过滤
  BlockNode 的 text ∈ noise 且在 strip 区 → 移除
  独有内容(strip 区但不重复) → 保留
```

**与 LayoutFilter 的关系**：LayoutFilter（pipeline stage）先做粗滤（固定 8% strip，可能误删）；本函数做**后处理精滤**——只移除**跨页重复**的幸存块。独有内容即使落在 strip 区也保留。

### 4.4 两路调用对比

| 步骤 | edited-PDF | OCR |
|---|---|---|
| calibrate | `use_height_fallback=False`（保留 AutoLevel 级） | `use_height_fallback=True`（高度聚类 + 大标题→metadata） |
| join_cross_page | ✅ | ✅ |
| filter_cross_page_noise | ✅ | ✅ |

---

## 5. structure.assemble（章节树构建）

**职责**：从 content（HeadingNode.level 已定）构建 section_tree + block_to_section。

```python
def build(content, toc_entries=None, keep_toc=False):
    root = SectionNode(id="sec_root", title="ROOT", level=0)
    stack = [root]
    for block in content:
        if isinstance(block, HeadingNode):
            level = min(max(block.level, 1), 9)
            while stack[-1].level >= level: stack.pop()
            sec = SectionNode(level=level, heading_node_id=block.id, ...)
            stack[-1].subsections.append(sec); stack.append(sec)
        elif isinstance(block, TocNode):
            continue  # 不参与建树
        else:
            stack[-1].block_ids.append(block.id)
    return root, block_to_section
```

- 单遍 O(n)；空间 O(d)（d=最大嵌套深度）
- 可选 `toc_entries` 校准 level（TOC 信号）
- `keep_toc=True` 产出 TocNode（默认不进 content）

**assemble 是 section_tree / block_to_section 的唯一生产者**（禁止旁路修改）。

---

## 6. 输出层

### 6.1 export

| 函数 | 格式 | 说明 |
|---|---|---|
| `to_json(doc)` | LogicalDocument JSON (exclude_none) | **规范输出**，可往返 |
| `to_markdown(doc)` | Markdown | 遍历 section_tree；`#`×level；表格管道格式；列表 `- `/`1. `；`*`→`\*` 转义；图片 `![alt](id)` |
| `to_plain_text(doc)` | 纯文本 | 按 content 序；表格 `\t` 连接 |
| `to_jsonl(doc)` | JSONL | 每行一个 block（**非规范**，兼容旧接口） |

### 6.2 debug/viz

| 函数 | 说明 |
|---|---|
| `visualize(doc, source_path, out_dir, mode)` | bbox 叠加图（PDF/OCR 有底图）/ 结构树（docx 无 bbox） |
| `visualize_debug_dir(debug_dir, source_path)` | 过程调试：每 stage×page 一图 + 阶段对比图 |
| `visualize_batch(sources)` | 批量 |

- **源感知**：PDF/OCR 有 provenance.bbox → 叠加视图；docx 无 bbox → 结构树
- **坐标校准**：bbox 已在后处理从服务空间换算到 PDF 点空间；debug 渲染时 `×(dpi/72)` → 像素

---

## 7. 配置点汇总

| 配置 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `_PARAGRAPH_BREAK_SPACING_RATIO` | merge.py | **1.5** | 段落合并阈值（间距 > 行距×此 → 分段） |
| `DOCUMENT_RATIO` | pdf_detect.py | 0.7 | editable/scanned 判定阈值 |
| `DOC_TITLE_RATIO` | heading.py | 1.8 | 大标题判定（bbox_h / body_h ≥ 此） |
| `_HEIGHT_LEVELS` | heading.py | [(1.6,1),(1.3,2),(1.15,3),(1.05,4)] | 无编号高度聚类层级阈值 |
| `strip_ratio` | heading.py filter_cross_page_noise | 0.10 | 页眉/页脚 strip 区比例 |
| `LAYOUT_DPI / PDF_DPI` | layout_filter.py | 136 / 72 | 版面坐标换算 |
| `IMAGE_MIN_AREA` | config.py | 1000.0 | 图片最小面积(pt²) |
| `OCR_*` env | _config.py | 见 §3.6 | OCR 服务配置 |

---

## 8. 三层职责对比

| 维度 | Pipeline Stage | 共享后处理 | structure.assemble |
|---|---|---|---|
| **操作对象** | element dict (font/size/bbox/span) | BlockNode (provenance/level/runs) | BlockNode → SectionNode 树 |
| **抽象层** | pre-IR（重建结构） | post-IR（校准结构） | post-IR（组装层级） |
| **作用域** | 按页(local) / 全文档(global) | **文档级** | **文档级** |
| **两路共用** | ❌（仅 edited-PDF） | ✅（edited + OCR） | ✅ |
| **典型处理** | BodyAnalysis 基准、AutoLevel 评分、LayoutFilter 过滤、Merge 合并 | 编号定级、标题合并、跨页拼接、页眉过滤 | 栈算法建章节树 |

**本质**：Pipeline 在"原始元素"层修字号/位置/分类（重建结构）；后处理在"已成 IR 节点"层修层级/合并/过滤（校准结构）；assemble 建层级树。三个层次，互不替代。
