# 老格式 Office 输入支持设计（识别 + 归一化转换）

| 项 | 内容 |
|---|---|
| 日期 | 2026-09-01 |
| 状态 | 已实施（2026-09-16，112 验收通过） |
| 范围 | /parse-pdf 服务端老格式识别 + LibreOffice 归一化转换 |
| 触发案例 | `D:\可行性报告.docx`（OLE2 二进制 .doc 错标）→ 500「不支持的源格式：bytes」 |

---

## 0. 背景与问题

Chai 调用 d2c `/parse-pdf` 时传入二进制 .doc（常见错标为 .docx）：

1. `_route_source_type` 嗅探表无 OLE2 魔数 → `UnsupportedFormatError: 不支持的源格式：bytes`，**报错无文件名**，调用方无从排查；
2. 即便识别出 .doc，服务也无转换能力（只认 PDF/docx/md/txt/图片五条路径）；
3. 实测（2026-08-31，可行性报告）：Word COM 转出的 docx 图片全为 VML（`w:pict`/`v:imagedata`），d2c DocxExtractor 不认 → 图片全丢，见 memory `doc-vml-image-gap`。

**目标**：所有 Office 老格式输入有明确出路——可解析的转换后解析，不可解析的 400 带文件名 + 指纹诊断，杜绝谜语 500。

**分期（已定）**：本期做识别层 + 转换层 + 文档/演示老格式支持；**xls/xlsx 检测到返回 400「表格格式即将支持」**，xlsx/pptx 专用 extractor 二期。

---

## 1. 决策记录

| 决策点 | 结论 | 备选与理由 |
|---|---|---|
| 范围 | 所有 Office 老格式（doc/rtf/wps/ppt + 错标件） | .doc 最常见，但方案按格式家族一次做对 |
| 转换器 | LibreOffice headless，装 d2c 容器内 | Word/WPS COM 已排除（依赖本机 Office）；独立转换服务多一个运维面 |
| 分期 | xls/xlsx 本期明确拒绝（400 即将支持） | 硬转 PDF 表格被打印分页切碎；xlsx extractor 二期 |
| 路由顺序 | **扩展名优先快路径，指纹识别只在异常时兜底** | 现有正常文件 100% 走原路零扰动，回归风险最小 |
| 转换层位置 | 前置归一化层（路由之前），不新增 SourceType/extractor | LegacyExtractor 封装名实不符（本体只是转换垫片） |
| doc 转换目标 | 默认 docx，`DOCUMENT2CHUNK_DOC_TARGET` 可切 pdf；**实施第一步实测 LibreOffice 转出图片是 DrawingML 还是 VML，VML 则默认切 pdf** | docx 路标题层级准（真样式/大纲级别）；pdf 路图片稳但标题误判多（2026-08-31 实测） |
| 演示格式 | .ppt 与 .pptx 统一转 PDF 走现有 PdfExtractor | pptx extractor 二期才有，转 PDF 本期即出结果 |
| 识别器 | **两级串联：手搓 zipfile+olefile 规则层 → magika 惰性兜底** | libmagic 出局（OLE2 兄弟盲 + 系统依赖）；magika 单用 RTF 误判 txt 且 40MB 常驻不合理 |
| 新增依赖 | `olefile`（130KB 纯 Python）+ `magika`（含 onnxruntime，容器 +40MB） | 无系统包 |

### 识别器实测依据（2026-09-01，本机）

| 样本 | 手搓规则 | magika 1.0.3 | libmagic (file) |
|---|---|---|---|
| OLE2 .doc（可行性报告） | 流名 `WordDocument` → doc ✅ | `doc` (0.95, 5ms) ✅ | "Composite Document File V2" **兄弟不分** ⚠️ |
| 真 docx / pdf | 条目名/魔数 ✅ | `docx`/`pdf` ✅ | ✅ |
| zip 家族三分（xlsx/pptx/纯zip） | 条目名 ✅ | ✅ 实测全对 | — |
| RTF | `{\rtf` ✅ | **误判 txt** ❌ | — |
| 依赖 | 130KB 零系统依赖 | onnxruntime 硬依赖 +40MB | 系统 libmagic1 + Windows dll 坑 |

---

## 2. 架构

```
新模块 ① src/document2chunk/format_detect.py   内容指纹识别器（规则层 + magika 兜底）
新模块 ② src/document2chunk/legacy_convert.py  LibreOffice 归一化转换执行器
改动   ③ serve.py / api.py `_chai_parse`       快路径守卫 → 异常时识别→转换→重路由
部署   ④ Dockerfile.d2c                        + libreoffice headless + fonts-noto-cjk
现有 extractor / postprocess                    零改动
```

### 2.1 格式策略矩阵

识别结果（内容指纹）→ 处置：

| 识别为 | 处置 | 解析落点 |
|---|---|---|
| PDF / 图片 / md / txt | 直接解析 | 现有路径 |
| zip + `word/document.xml`（docx） | 直接解析 | DocxExtractor |
| zip + `ppt/presentation.xml`（pptx） | 转 PDF | PdfExtractor |
| zip + `xl/workbook.xml`（xlsx） | 400「表格格式即将支持」 | 二期 |
| OLE2 + `WordDocument`（doc） | 转 docx（可配 pdf） | DocxExtractor / PdfExtractor |
| OLE2 + `PowerPoint Document`（ppt） | 转 PDF | PdfExtractor |
| OLE2 + `Workbook`（xls） | 400「表格格式即将支持」 | 二期 |
| `{\rtf` 文本头（rtf） | 转 docx | DocxExtractor |
| OLE2 + WPS 流（wps，Kingsoft 变体） | 尽力转 docx，失败明确报错 | DocxExtractor |
| 无法识别 | 400，**文件名 + 魔数诊断 + magika 意见** | — |

### 2.2 识别层：两级串联（fast path / slow path）

```
bytes → ① 规则层（手搓 zipfile+olefile，µs 级）
 │    ├─ %PDF- / 图片魔数 / {\rtf        → 定
 │    ├─ PK + zip 条目名三分              → 定
 │    └─ D0CF11E0 + OLE2 流名三分         → 定
 ├─ 命中 → 返回（95%+ 请求到此，永不触模型）
 └─ 未命中 → ② magika（惰性 import，~5ms/次）
        ├─ label ≠ unknown → 采纳（边缘格式兜底，如 wps）
        └─ unknown → 弃权（诊断信息拼接两层意见）
```

串联纪律：

1. **不重叠不仲裁**——magika 仅在规则层弃权后参与，无「打架」问题；
2. **magika 惰性 import**——顶层不加载 onnxruntime，多数请求零模型成本；
3. **RTF 定死规则层**——magika 误判 txt，由 `{\rtf` 前缀截胡。

识别结果结构：`(kind, ext)`，kind ∈ {PDF, DOCX, DOC, XLS, XLSX, PPT, PPTX, RTF, WPS, IMAGE, TEXT_MD, TEXT_TXT, UNKNOWN}；输入 bytes + 可选 filename（仅用于诊断与日志，**不参与判定**）。

---

## 3. 数据流与错误处理

### 3.1 路由流程（serve 层）

```
输入 (filename / file_path)
 → ① 后缀为现代格式（pdf/docx/md/txt/图片）？
 │     → 现有解析 ─ 成功 → 返回（快路径，与现状完全一致）
 │              └ 抛格式类异常（UnsupportedFormatError / InvalidDocxError /
 │                InvalidPdfError 等打开·解包期失败）→ ③ 指纹兜底 ↓
 → ② 后缀为老格式（doc/rtf/ppt/pptx/wps）或 xls/xlsx？
 │     → 直接进转换分支（xls/xlsx → 400 即将支持）
 → ① 后缀未知 / zip 模式无文件名 → ③ 指纹兜底 ↓

③ 指纹识别（format_detect）
   ├─ doc/rtf/wps → legacy_convert → docx → 现有管线
   ├─ ppt/pptx   → legacy_convert → pdf  → 现有管线
   ├─ xls/xlsx   → 400「表格格式即将支持」（带文件名）
   └─ pdf/docx/图片… → 按识别结果重路由解析
       仍失败 → 最终错误 + 指纹诊断
```

触发指纹兜底的异常**限定为格式类**（打开/解包阶段），不捕内容解析错误——避免过宽掩盖 bug。

### 3.2 转换执行（legacy_convert）

- 临时落盘，**按识别出的真实格式命名扩展名**（.docx 实为 OLE2 → 落成 `.doc`），soffice 才能正确识别；
- `soffice --headless --norestore -env:UserInstallation=file:///tmp/<uuid> --convert-to docx|pdf --outdir <tmp> <file>`；
  - **独立 UserInstallation profile**：headless LibreOffice 全局锁，不隔离并发必死锁；
- `subprocess.run(timeout=DOCUMENT2CHUNK_SOFFICE_TIMEOUT)`，超时 kill 进程树并 500；
- 转换产物读回 bytes → 交现有 `_route_source_type` → 正常管线；
- 临时目录 finally 清理。

错误映射（全部带文件名 + 检测格式）：

| 场景 | 状态码 |
|---|---|
| soffice 未安装/不可执行 | 503 转换器未就绪 |
| 转换超时 | 500（附时长与超时配置提示） |
| 产物缺失/0 字节 | 422 无法转换：<文件名>（检测为 .doc） |
| 识别不出且 magika unknown | 400 + 文件名 + 魔数前 16 字节 hex + 两层识别意见 |
| xls/xlsx | 400 表格格式即将支持 |

### 3.3 配置项（.env，全部有默认值）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DOCUMENT2CHUNK_DOC_TARGET` | `docx` | doc/rtf/wps 转换目标，`pdf` 可切 |
| `DOCUMENT2CHUNK_SOFFICE_TIMEOUT` | `120` | 单次转换超时秒数 |

### 3.4 部署（Dockerfile.d2c）

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-core libreoffice-writer libreoffice-impress libreoffice-calc \
    fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*
```

**字体坑**：容器无中文字体则转 PDF 中文全豆腐块，`fonts-noto-cjk` 必装。pptx→pdf 依赖 impress 组件，calc 为二期 xls 预留。镜像约 +400MB（LibreOffice）+40MB（onnxruntime）。

---

## 4. 测试与验收

1. **单测 — format_detect**：fixtures = 真 OLE2 .doc（可行性报告入库）、最小 docx/xlsx/pptx、rtf 文本、pdf/图片、空/截断/随机字节（识别不出且不崩溃）、错标件（.docx 后缀实为 OLE2 → DOC）。magika 路径 mock（CI 不装 onnx）。
2. **单测 — legacy_convert**：默认 mock soffice（断言独立 profile、--convert-to、timeout 参数；失败/超时/产物缺失→各异常类型带文件名）；真 soffice 用例 skipif。
3. **集成 — serve 层**（回归门禁优先）：

| 用例 | 预期 |
|---|---|
| 正常 .docx / .pdf 改动前后产物 diff | **完全一致**（快路径零扰动） |
| 错标 .docx（实为 OLE2） | 200 + result.md |
| 真 .doc / .rtf | 200 |
| .ppt | 200（PDF 路径，中文无豆腐块） |
| .xls / .xlsx | 400 即将支持 + 文件名 |
| 裸 PDF bytes（无文件名） | 200 |
| 乱字节 | 400 + 指纹诊断 |

4. **实施第一步 — VML 验证实验**：装 LibreOffice → 可行性报告转 docx → 统计 `a:blip` vs `v:imagedata`：DrawingML → 默认 docx；VML → 默认切 pdf（改默认值，不改代码）。同时人工对比标题样式保留度。
5. **112 端到端**：镜像 soffice --version 通过 → rebuild（.env OCR token 在位）→ 本机 curl 错标可行性报告 → 200 → result.md 抽查标题/图片。

**DoD**：单测集成全绿 + 现有格式回归零差异 + 错标件端到端出合格产物（含图片）。

---

## 5. 二期待办（登记不做）

- xlsx/pptx 专用 extractor（openpyxl / python-pptx，接 BlockNode 统一 postprocess）；
- 届时 xls/xlsx 400 提示改为直接解析；pptx 可改走 pptx extractor（或维持 PDF）；
- magika 模型/规则层覆盖面扩张后复盘串联边界。
