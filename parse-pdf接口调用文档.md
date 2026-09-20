# Document2Chunk `/parse-pdf` 接口调用文档

| 项 | 内容 |
|---|---|
| 服务 | Document2Chunk（d2c 引擎） |
| 当前部署 | `http://128.23.67.112:9301`（Docker，容器内 8000） |
| 接口路径 | `POST /parse-pdf` |
| 文档性质 | **调用方视角**的接口参考，描述代码实际行为（`src/document2chunk/api.py`、`serve.py`） |
| 别名 | **`/parse-doc`（2026-09-20 新增）与本接口完全同契约**，路径互换即可；独立文档见《parse-doc接口调用文档.md》 |
| 关联文档 | 《Document2Chunk-PDF解析接口对接需求文档.md》（Chai 对接视角，含 CLI 模式与配置项） |

---

## 1. 接口概述

`/parse-pdf` 将 PDF / DOCX / 扫描件图片 / Markdown / 纯文本解析为 **Markdown 全文（result.md）+ 提取图片**，供下游 RAG 分段、向量化使用。

- **调用模式**：同步阻塞——请求返回即解析完成，无任务 ID、无轮询
- **幂等**：同一文件多次解析结果一致，失败可安全重试
- **无状态**：服务不保存调用方任何上下文

### 1.1 两种模式（二选一，由请求字段决定）

| 模式 | 触发条件 | 适用场景 | 响应 |
|---|---|---|---|
| **zip 模式** | 请求带 `file`（或 `document`）文件字段，无 `file_path` | 跨机器调用（默认推荐） | `application/zip` 二进制流 |
| **路径模式** | 请求带 `file_path` 字段 | 服务与调用方**共享文件系统** | `{"status": "ok"}` |

> 同时携带 `file_path` 和 `file` 时，**按路径模式处理**（`file_path` 优先）。

---

## 2. 探活

```
GET /health
```

响应 `200`：

```json
{"status": "ok", "version": "0.1.0"}
```

---

## 3. 通用约定

| 项 | 约定 |
|---|---|
| Content-Type | 必须 `multipart/form-data`，否则 `400` |
| 超时 | 同步阻塞；扫描件走 OCR 耗时较长，**建议客户端读超时 ≥ 10 分钟**（对接方 Chai 侧上限 30 分钟） |
| 文件大小 | 服务端未设硬限制；建议单文件 ≤ 1024MB（对齐 Chai 侧 multipart 上限） |
| result.md 编码 | UTF-8（**无 BOM**） |
| 图片引用 | result.md 内用**相对路径**引用图片 |
| 失败重试 | 调用方可对非 2xx 响应安全重试（幂等） |

### 3.1 通用可选参数 `demote`

两个模式均支持表单字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `demote` | 字符串 | 否 | 传 `true`（大小写不敏感）开启**降误检**：标题文本超过 60 字符且以句号（`。！？.!?`）结尾的"伪标题"降级为正文段落。其余任何值均视为关闭 |

---

## 4. zip 模式（推荐）

### 4.1 请求

```
POST /parse-pdf HTTP/1.1
Content-Type: multipart/form-data
```

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件（binary） | 是* | 待解析文件二进制。字段名用 `file`，`document` 亦可（二者取先命中者） |
| `demote` | 字符串 | 否 | 见 §3.1 |

> \* **务必设置带扩展名的文件名**（multipart filename）。格式路由依赖扩展名；`.md` / `.txt` **只能**靠文件名识别，缺文件名或无扩展名时将按魔数嗅探，嗅探不出则 `400`。

### 4.2 响应

**成功**：`200`，`Content-Type: application/zip`，响应体为 zip 字节流（直接二进制，非 base64）：

```
result.zip
├── result.md          ← 必须，位于 zip 根目录（条目名就是 result.md）
└── images/            ← 可选，提取的图片
    ├── fig_001.png
    └── ...
```

- `result.md` 为**全文**（主文 + 附件拼接），UTF-8 无 BOM
- 图片引用固定 `images/` 前缀：`![描述](images/fig_001.png)`
- 复杂表格（含合并单元格）默认渲染为 HTML `<table>` 文本（保留 colspan/rowspan），不依赖截图；简单表格为 Markdown 管道表格

**失败**：非 2xx + `{"detail": "可读错误信息"}`，见 §7。

### 4.3 调用示例

**curl**：

```bash
curl -X POST http://128.23.67.112:9301/parse-pdf \
  -F "file=@/path/to/document.pdf" \
  -o result.zip

# 开启降误检
curl -X POST http://128.23.67.112:9301/parse-pdf \
  -F "file=@/path/to/document.pdf" \
  -F "demote=true" \
  -o result.zip

# 验证产物
python -c "import zipfile; z=zipfile.ZipFile('result.zip'); print(z.namelist()); print(z.read('result.md').decode('utf-8')[:200])"
```

**Python（requests）**：

```python
import requests

with open("document.pdf", "rb") as f:
    resp = requests.post(
        "http://128.23.67.112:9301/parse-pdf",
        files={"file": ("document.pdf", f)},   # 元组首元素即 filename，必须带扩展名
        data={"demote": "false"},
        timeout=600,                            # 扫描件较慢，给足超时
    )
resp.raise_for_status()

import io, zipfile
with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
    markdown = zf.read("result.md").decode("utf-8")
    images = [n for n in zf.namelist() if n.startswith("images/")]
```

---

## 5. 路径模式（共享文件系统）

服务直接读取调用方落盘的文件，并把产物**回写**到调用方指定的目录。适用于服务与调用方同机或挂载共享盘的场景。

### 5.1 请求

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | 字符串 | 是 | 原始文件的**绝对路径**（服务所在文件系统视角） |
| `output_dir` | 字符串 | 是 | 产物目录**绝对路径**，服务写入 `{output_dir}/result.md` |
| `image_dir` | 字符串 | 是 | 图片目录**绝对路径**，服务**自行创建**并写入图片 |
| `demote` | 字符串 | 否 | 见 §3.1 |

### 5.2 响应

**成功**：`200`

```json
{"status": "ok"}
```

**失败**：非 2xx + `{"detail": "可读错误信息"}`，见 §7。

### 5.3 产物与图片引用规则

```
{output_dir}/
├── result.md          ← UTF-8 全文
└── {image_dir}/       ← 服务自行创建
    ├── fig_001.png
    └── ...
```

- result.md 中图片引用前缀为 **`image_dir` 的末级目录名**（basename）。例如 `image_dir=/data/out/my_images` 时引用为 `![x](my_images/fig_001.png)`。
- 因此 **`image_dir` 建议命名为 `images`**（即 `{output_dir}/images`），与 zip 模式产物结构保持一致，下游处理逻辑可复用。

### 5.4 调用示例

```bash
curl -X POST http://128.23.67.112:9301/parse-pdf \
  -F "file_path=/data/chai/upload/20260724/abc.pdf" \
  -F "output_dir=/data/chai/upload/20260724/abc_out" \
  -F "image_dir=/data/chai/upload/20260724/abc_out/images"

# 检查结果
head -20 /data/chai/upload/20260724/abc_out/result.md
ls /data/chai/upload/20260724/abc_out/images/
```

---

## 6. 支持格式与路由规则

按**文件扩展名**路由（zip 模式看 multipart filename；路径模式看磁盘文件名）；无扩展名时按**魔数嗅探**兜底。

| 输入 | 判定依据 | 处理引擎 | 说明 |
|---|---|---|---|
| `.pdf` | 扩展名 | 自动二分 | 先检测文本层：可编辑 PDF → PyMuPDF+pdfplumber；扫描/混合 PDF → 远程 PaddleOCR |
| `.docx` | 扩展名 | DOCX 引擎（lxml 直读 OpenXML） | |
| `.png` `.jpg` `.jpeg` `.bmp` `.tif` `.tiff` `.gif` `.webp` | 扩展名 | OCR 引擎 | |
| `.md` | 扩展名（**仅此判定**） | 直通 | 不进解析管线：解码（UTF-8 优先 / GB18030 回退）→ 标题复原 → 以 UTF-8 无 BOM 输出 result.md；解码失败回退为原始字节直通 |
| `.txt` | 扩展名（**仅此判定**） | 转录 | 不进解析管线：解码（UTF-8 优先 / GB18030 回退，失败则 `400`）→ 标题复原 → 转为 UTF-8 无 BOM 的 result.md |
| 无扩展名 | 魔数嗅探 | — | `%PDF-` → PDF 判定；PNG/JPEG/BMP/TIFF/GIF 魔数 → OCR；`PK`（ZIP 容器）→ DOCX；嗅探不出 → `400` |

**OCR 说明**：扫描件经宿主 PaddleOCR 服务（模型 `vl`）识别，速度显著慢于可编辑 PDF 的文本抽取。

---

## 7. 错误响应

错误响应体统一为 FastAPI 默认结构 `{"detail": "..."}`（注意：与需求文档示例中的 `{"status":"error","message":...}` 不同，**以本文档为准**）。

| HTTP 状态码 | 触发场景 | detail 示例 |
|---|---|---|
| `400` | 请求非 `multipart/form-data` | `需 multipart/form-data` |
| `400` | 路径模式缺 `output_dir` / `image_dir` | `路径模式需 file_path + output_dir + image_dir` |
| `400` | 路径模式文件不存在 | `文件不存在: /data/xxx.pdf` |
| `400` | zip 模式缺文件字段 | `zip 模式缺少 file 字段` |
| `400` | 格式无法识别 / 路由失败 / txt 无法解码 | `不支持的源格式：xxx`、`txt 转录失败：内容既非 UTF-8 也非 GB18030` |
| `422` | 文件损坏等文档级解析错误 | 具体 `InvalidDocxError` / `InvalidPdfError` 等消息 |
| `500` | 解析过程未预期异常 | `<异常类型>: <消息>` |
| `503` | 服务端依赖未就绪（如 OCR 服务不可达） | `extractor 未就绪/依赖缺失：...` |

**重试建议**：`400` 为调用方错误，重试无意义；`422` 确认文件完整性后再重试；`500` / `503` 可安全重试（幂等）。

---

## 8. 相关能力速览

| 能力 | 说明 |
|---|---|
| `GET /health` | 探活（§2） |
| `POST /parse` | 同机部署的 mineru2doc（MinerU 引擎）代理端点，契约与 `/parse-pdf` 一致，引擎不同 |
| `POST /parse-json` | 库级接口：返回文档 IR（JSON）+ markdown，供需要结构化树的调用方 |
| CLI | `python -m document2chunk cli --input <文件> --output <产物目录> --images <图片目录> [--demote]`，成功 exit 0；适合与调用方同机部署的场景（Chai CLI 模式配置见需求文档 §7） |

---

## 9. 契约自检清单

**zip 模式**：
- [ ] `POST` + `multipart/form-data`，文件字段名 `file`（或 `document`）
- [ ] multipart filename 带**扩展名**（`.md`/`.txt` 仅靠文件名识别）
- [ ] 客户端超时 ≥ 10 分钟
- [ ] 校验 zip **根目录**存在 `result.md`（UTF-8 无 BOM）
- [ ] zip 内不含 `../` 路径穿越条目（服务端保证）

**路径模式**：
- [ ] 三个绝对路径字段齐全：`file_path` + `output_dir` + `image_dir`
- [ ] `image_dir` 命名为 `images`（图片引用前缀 = image_dir 目录名）
- [ ] 成功响应为 `{"status": "ok"}`；`output_dir` 出现 `result.md`

**通用**：
- [ ] 失败响应对应 `{"detail": "..."}` 结构（非 `{"status":"error"}`）
- [ ] 需要降误检时传 `demote=true`
- [ ] 同步调用，不做任务轮询
