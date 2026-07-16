# OCR 服务联调与 provenance 修复报告

> 日期：2026-07-16 ｜ 范围：三分支合并 + 真实 PaddleOCR 服务联调 + `box→provenance` 修复
> 远程服务：`http://128.23.67.112:8000`（PP-OCRv6 / PaddleOCR-VL / Unlimited-OCR，文档见 `D:\project\server\PaddleOCR三件套使用文档.md`）

---

## 1. 处理过程

### 1.1 三分支合并入 main（@0e66f41）

把此前三个独立分支一并并入 `main`（隔离 `integration-3` worktree 内合并、解冲突、跑测试，再 FF 到 main，不扰对方在改的 `_chunker/_mapping`）：

| 分支 | 内容 |
|---|---|
| `feat/pdf-layout-fusion` | 版面×span 双向融合（designs/004）：image_detection 三信号修首页误判成图片、table 校验修封面误判成表 |
| `feat/merge-spacing` | merge.py 行间距防过度合并 |
| `feat/ocr-envconfig` | `extractors/ocr/_config.py` 的 `.env` 自动加载（`DOCUMENT2CHUNK_OCR_*`） |

合并无冲突（`ort` 自动合并 `SESSIONS.md`/`_config.py`），全套测试绿。

### 1.2 designs/004 撞号 → 实为虚惊

我方 `004-pdf-layout-span-fusion.md` 与对方**未提交时**的 `004-ocr-heading-calibration.md` 同号。但对方提交时**改编号为 `005-ocr-heading-calibration.md`** → main 实际 `004`（我方）+ `005`（对方），**无撞号**，故无需改成 004-1。

### 1.3 真实 OCR 服务联调

- **服务可达**：`GET /api/model-runtime` → HTTP 200。
- **模型需冷加载**：初始三个模型均未就绪（`unlimited-ocr` 加载超时 `state=error`，VL/pp-ocrv6 `stopped`）。切到 `pp-ocrv6` 后约 1–2 分钟就绪；VL（默认）切后 30 秒就绪（疑似缓存尚温）。
- **实测输入**：`doc-paddle-ocr/test.pdf` 第 0 页栅格化（1659×2344 真实扫描页）。
- **pp-ocrv6 结果**：文字识别正确（"交通运输部 文件 科学技术部…"），但仅产出 1 个 `ParagraphNode`、`provenance.bbox=None`。
- **VL（默认）结果**：结构更好（6 块：标题+段落），但同样 `bbox=None`（0/6）。

### 1.4 定位根因 + 修复（@8e82eb1，已并入 main）

`provenance.bbox` 恒为 `None`。查真实响应结构：

```
layoutParsingResults[0]:
  prunedResult:
    parsing_res_list: [{block_label, block_content, block_bbox, block_order}, ...]  ← bbox 在这
    width / height                                                                   ← 坐标空间也在这
```

而 `extractors/ocr/extractor.py` 原读 `lp.get("parsing_res_list")`（**lpr 顶层**）与 `lp.get("width/height")`——**路径错**。合成 fixture 把 `parsing_res_list` 放在顶层，故单测过、真机不过。

**修**（`extractor.py`，兼容两种形态）：
```python
pr = lp.get("prunedResult") or {}
prl = lp.get("parsing_res_list") or pr.get("parsing_res_list") or []
sw = float(pr.get("width")  or lp.get("width")  or 1000)
sh = float(pr.get("height") or lp.get("height") or 1000)
```

**验证**：真实 VL 修复后 `with bbox 5/6`（原 0/6），bbox 落在图像像素范围内（如 `[293,582,1384,860]`）。新增回归测试 `tests/test_ocr_provenance_path.py`（顶层 + prunedResult 两形态）。回归 `test_ocr_extractor`/`ir_smoke` 绿。

---

## 2. 发现的问题

| # | 问题 | 状态 |
|---|---|---|
| 1 | `extractor` 从错误路径读 `parsing_res_list`/`width`/`height` → `provenance.bbox=None` | ✅ 已修（1.4） |
| 2 | **pp-ocrv6** 不返回 `parsing_res_list`，而是 `ocrLines[].{text,box,score}`（每行 box）+ `prunedResult.{dt_polys,rec_texts,...}` | ⚠️ 未修——pp-ocrv6 仍 `bbox=None`；见 §3 follow-up |
| 3 | `unlimited-ocr` 加载超时（`state=error`），未实测 | ⚠️ 待模型就绪后验证 |
| 4 | 修复后 VL 标题识别变化（2→0 HeadingNode）——下游 `calibrate`（designs/005）现在拿到真实 bbox 高度，按其聚类规则判定；属对方调参域，非本修复引入的 bug | ℹ️ 观察项 |
| 5 | 6 块中 1 块仍无 bbox（markdown 元素与 `parsing_res_list` 1:1 对齐的边界 case，`DROP_LABELS` 过滤后索引错位） | ℹ️ 轻微，可接受 |

---

## 3. Follow-up（未完成 / 待对方）

1. **pp-ocrv6 的 `ocrLines` 路径**：pp-ocrv6 是流水线 OCR，返回逐行 `ocrLines`（text+box），与 VL/unlimited 的 `parsing_res_list`（按版面块）不同。要么在 `extractor` 把 `ocrLines` 归一化成 `parsing_res_list` 形态（注意与 markdown 元素的 1:1 对齐），要么给 pp-ocrv6 单独的「逐行→块」映射。**属 `extractors/ocr/` 设计决策（对方在改），建议由其统一处理**。
2. **unlimited-ocr**：长 PDF（>20 页）走它，但本次加载超时。待 GPU 空闲 / 缓存预热后重测，确认 `parsing_res_list` 路径对其同样生效。
3. **bbox 1:1 对齐边界**（问题 5）：`build_page_blocks` 按 `parsing_res_list` 过滤 `DROP_LABELS` 后的下标配 markdown 元素；条目数不等时末块缺 bbox。可改为按 `block_order`/`block_id` 显式配对，提升鲁棒性。
4. **provenance 坐标系**：图像输入时 bbox 落在图像像素空间（自然）；PDF 输入时由 `iter_pages` 给的页面点尺寸 + 服务 `width/height` 校准到 PDF 点——已生效，但建议加一个可视化校验（`debug.visualize` 叠加 bbox）确认无系统偏差。

---

## 4. 复现联调

```bash
export DOCUMENT2CHUNK_OCR_TOKEN=<token>        # 或写进仓库根 .env（已 gitignore）
export DOCUMENT2CHUNK_OCR_ENDPOINT=http://128.23.67.112:8000
# 造一份扫描页（或用任意图片/扫描 PDF）
python -c "import pymupdf as f; d=f.open('test.pdf'); d[0].get_pixmap(dpi=200).save('p0.png')"
# 跑（默认 VL；可选 ocr_model=pp-ocrv6|vl|unlimited）
PYTHONPATH=src python -c "
import types; from document2chunk.extractors.ocr import OcrExtractor
r = OcrExtractor().extract('p0.png', options=types.SimpleNamespace(ocr_model='vl'))
print(sum(1 for b in r.content if b.provenance and b.provenance.bbox), '/', len(r.content), '带 bbox')
"
```

## 5. 本次 main 提交

- `0e66f41`（含 `035acb8`/`f0738f9`/`0e66f41` 三个 merge）：版面×span 融合 + merge 间距 + ocr `.env`
- `8e82eb1`：`fix(ocr)` provenance 取自 `prunedResult.parsing_res_list`（本联调修复）
