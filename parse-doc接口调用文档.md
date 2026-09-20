# Document2Chunk `/parse-doc` 接口调用文档

| 项 | 内容 |
|---|---|
| 服务 | Document2Chunk（d2c 引擎） |
| 当前部署 | `http://128.23.67.112:9301`（Docker，容器内 8000） |
| 接口路径 | `POST /parse-doc` |
| 文档性质 | **调用方视角**的接口参考，描述代码实际行为（`src/document2chunk/api.py`、`serve.py`） |
| 关系声明 | **`/parse-doc` 是 `/parse-pdf` 的别名**（2026-09-20 新增）：同一处理逻辑、同一参数、同一响应、同一错误码。本文档为独立成册的调用参考；《parse-pdf接口调用文档.md》同样适用于 `/parse-doc`（把路径替换即可） |

---

## 1. 接口概述

`POST /parse-doc` 将 **PDF / DOCX / 扫描件图片 / Markdown / 纯文本**（是的，尽管名字带 doc，实际与 `/parse-pdf` 完全同能力）解析为 **Markdown 全文（result.md）+ 提取图片**，供下游 RAG 分段、向量化使用。

- **与 `/parse-pdf` 的关系**：别名。服务端两个路径指向同一个处理函数，行为逐字节一致；新增它只是为了给调用方一个语义更中性的入口名，**不存在 doc 专属逻辑**
- **调用模式**：同步阻塞——请求返回即解析完成，无任务 ID、无轮询
- **幂等**：同一文件多次解析结果一致，失败可安全重试
- **无状态**：服务不保存调用方任何上下文

### 1.1 两种模式（二选一，由请求字段决定）

| 模式 | 触发条件 | 适用场景 | 响应 |
|---|---|---|---|
| **zip 模式** | 请求带 `file`（或 `document`）文件字段，无 `file_path` | 跨机器调用（默认推荐） | `application/zip` 二进制流（`Content-Disposition: attachment; filename="result.zip"`） |
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
POST /parse-doc HTTP/1.1
Content-Type: multipart/form-data
```

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件（binary） | 是* | 待解析文件二进制。字段名用 `file`，`document` 亦可（二者取先命中者） |
| `demote` | 字符串 | 否 | 见 §3.1 |

> \* **务必设置带扩展名的文件名**（multipart filename）。格式路由依赖扩展名；`.md` / `.txt` **只能**靠文件名识别，缺文件名或无扩展名时将按魔数嗅探，嗅探不出则 `400`。

### 4.2 响应

**成功**：`200`，`Content-Type: application/zip`，body 为 zip 二进制：

```
result.md              ← 全文 Markdown（UTF-8 无 BOM）
images/xxx.png         ← 提取图片（有图时）
```

**失败**：非 2xx + `{"detail": "可读错误信息"}`，见 §7。

### 4.3 curl 示例

```bash
curl -X POST "http://128.23.67.112:9301/parse-doc" \
  -F "file=@/path/to/report.docx" \
  -o result.zip

# 开启降误检
curl -X POST "http://128.23.67.112:9301/parse-doc" \
  -F "file=@/path/to/scan.pdf" \
  -F "demote=true" \
  -o result.zip
```

### 4.4 产物结构

```
result.zip
├── result.md
└── images/             ← 有图时存在
    └── fig_001.png
```

- result.md 中图片引用为相对路径：`![x](images/fig_001.png)`，与 zip 内目录一致，**解压后即可直接渲染**

---

## 5. 路径模式（共享文件系统时）

### 5.1 请求

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | 字符串 | 是 | 待解析文件**绝对路径**（服务端本地） |
| `output_dir` | 字符串 | 是 | 产物目录**绝对路径**，服务写入 `{output_dir}/result.md` |
| `image_dir` | 字符串 | 是 | 图片目录**绝对路径**，服务**自行创建**并写入图片 |
| `demote` | 字符串 | 否 | 见 §3.1 |

### 5.2 响应

**成功**：`200` + `{"status": "ok"}`

**失败**：非 2xx + `{"detail": "可读错误信息"}`，见 §7。

### 5.3 curl 示例

```bash
curl -X POST "http://128.23.67.112:9301/parse-doc" \
  -F "file_path=/data/chai/upload/20260920/abc.docx" \
  -F "output_dir=/data/chai/upload/20260920/abc_out" \
  -F "image_dir=/data/chai/upload/20260920/abc_out/images"
```

---

## 6. 支持的文件类型

| 类型 | 识别依据 | 链路 | 说明 |
|---|---|---|---|
| `.docx` | 扩展名 / `PK` 魔数 | 直解析 | 老 `.doc` / `.rtf` / `.wps` 内容（指纹识别）自动转换为 docx 后解析 |
| `.pdf` | 扩展名 / 魔数 | 可编辑直解析；扫描件/混合 → OCR | OCR 逐页送远程服务（页级并发 4） |
| `.png/.jpg/...` | 扩展名 / 魔数 | OCR | 图片作为单页处理 |
| `.md` | 扩展名（**仅此判定**） | 直通 | 解码（UTF-8 优先 / GB18030 回退，失败则 `400`）→ 标题复原 → result.md |
| `.txt` | 扩展名（**仅此判定**） | 转录 | 同上 |
| 无扩展名 | 魔数嗅探 | — | `%PDF-` → PDF；PNG/JPEG/BMP/TIFF/GIF → OCR；`PK` → DOCX；嗅探不出 → `400` |

---

## 7. 错误响应

错误响应体统一为 FastAPI 默认结构 `{"detail": "..."}`。

| 状态码 | 触发 | detail 示例 |
|---|---|---|
| `400` | 请求非 `multipart/form-data` | `需 multipart/form-data` |
| `400` | 路径模式缺 `output_dir` / `image_dir` | `路径模式需 file_path + output_dir + image_dir` |
| `400` | 路径模式文件不存在 | `文件不存在: /data/xxx.docx` |
| `400` | zip 模式缺文件字段 | `zip 模式缺少 file 字段` |
| `400` | 格式无法识别 / 路由失败 / txt 无法解码 | `不支持的源格式：xxx` 等 |
| `422` | 文件损坏等文档级解析错误；**病态 PDF 检测/提取超时被终止** | `PDF 可编辑性检测超过 120s 未完成——疑似病态 PDF…` 等 |
| `500` | 解析过程未预期异常 | `<异常类型>: <消息>` |
| `503` | 服务端依赖未就绪（如 OCR 服务不可达） | `extractor 未就绪/依赖缺失：...` |

**重试建议**：`400` 为调用方错误，重试无意义；`422` 确认文件完整性后再重试；`500` / `503` 可安全重试（幂等）。

---

## 8. 调用方检查清单

- [ ] multipart `filename` 带正确扩展名（`.md`/`.txt` 仅靠它识别）
- [ ] 读超时 ≥ 10 分钟
- [ ] zip 模式：`file`（或 `document`）字段为文件本身（非 base64 字符串）
- [ ] 路径模式：三个绝对路径字段齐全，`image_dir` 建议命名为 `images`
- [ ] 失败重试前先读 `detail`：400 类改请求，422 类查文件，500/503 才盲目重试

---

## 9. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-20 | `/parse-doc` 上线：`/parse-pdf` 的别名，契约与行为完全一致 |
