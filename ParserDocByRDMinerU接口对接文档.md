# ParserDocByRDMinerU 接口对接文档

| 项 | 内容 |
|---|---|
| 服务 | mineru2doc-bellm（引擎 `bellm`） |
| 名称释义 | **RD = 研发中心**（网关运营方）。ParserDocByRDMinerU 是需求方（Chai 侧）对这条通道的命名，即「经研发中心 MinerU 网关解析文档」的 ParserDoc 通道 |
| 当前部署 | `http://128.23.67.112:9302`（Docker 容器 `mineru2doc-bellm`，容器内 8000） |
| 接口路径 | `POST /parse` |
| 文档性质 | **调用方视角**的接口参考，描述代码实际行为（`mineru2doc/server.py`，`MINERU_ENGINE=bellm`） |
| 关系声明 | 与 `/parse`（9300，MinerU 3.4.2 本地引擎）、`/parse-doc`（9301，document2chunk d2c 引擎）三者**契约同源**（Chai 契约 v2.0：同双模式、同字段、同 `demote` 语义、同产物结构）、**引擎不同**（本服务经研发中心 bellm 网关解析） |

---

## 1. 接口概述

`POST /parse` 将 **PDF / PNG / JPG / JPEG**（仅此四种，见 §6 格式限制）经**研发中心 MinerU 网关（bellm）**解析为 **多级标题 Markdown 全文（result.md）+ 提取图片**，供下游 RAG 分段、向量化使用。

- **引擎**：研发中心 bellm 网关（`http://128.23.74.3:9091/bellm/MinerU`，服务端持有 Basic 凭证调用）。网关侧协议差异（认证头、data-url 前缀 base64 图片等）已由服务端适配，**调用方无感知**
- **调用模式**：同步阻塞——请求返回即解析完成，无任务 ID、无轮询
- **幂等**：同一文件多次解析结果一致，失败可安全重试
- **无状态**：服务不保存调用方任何上下文
- **鉴权**：本服务自身无鉴权（与 9300/9301 一致），调用方无需携带任何凭证或请求头

### 1.1 两种模式（二选一，由请求字段决定）

| 模式 | 触发条件 | 适用场景 | 响应 |
|---|---|---|---|
| **zip 模式** | 请求带 `file` 文件字段，无 `file_path` | 跨机器调用（默认推荐） | `application/zip` 二进制流（`Content-Disposition: attachment; filename="result.zip"`） |
| **路径模式** | 请求带 `file_path` 字段 | 服务与调用方**共享文件系统** | `{"status": "ok"}` |

> 同时携带 `file_path` 和 `file` 时，**按路径模式处理**（`file_path` 优先）——与 9300 一致。

---

## 2. 探活

```
GET /health
```

响应 `200`：

```json
{"status": "ok", "version": "0.1.0", "mineru_base_url": "http://host.docker.internal:9030", "engine": "bellm"}
```

> `engine` 字段标识当前引擎（本服务恒为 `"bellm"`；9300 为 `"local"`）。`mineru_base_url` 为历史字段，内容仍是本地引擎地址，**不随引擎切换**——判断引擎请看 `engine`。

---

## 3. 通用约定

| 项 | 约定 |
|---|---|
| Content-Type | 必须 `multipart/form-data` |
| 超时 | 同步阻塞。性能基线见 §6.2（42 页 PDF 约 28s）；服务内部对网关超时 300s，超时即回 `502`，不会无限等待——**建议客户端读超时 ≥ 6 分钟** |
| 文件大小 | 服务端未设硬限制；网关侧限额未实测，超大文件请先行试跑 |
| result.md 编码 | UTF-8（**无 BOM**） |
| 图片引用 | 契约约定为 result.md 内**相对路径**引用图片（`images/<hash>.jpg`，沿用 9300 契约约定），与 zip 内目录一致；但 bellm 引擎的实际引用格式与落盘布局**尚待带插图 PDF 实测定案**（两份实测样本 md 均无图片引用，见《HPC112对外接口明细调查》§8.4），定案前调用方不应假设解压即可直接渲染 |
| 失败重试 | 调用方可对非 2xx 响应安全重试（幂等）；但 400 类重试无意义，见 §7 |
| 鉴权 | 无。网关凭证是**服务端→网关**的内部凭证（环境变量注入），调用方无需感知 |

### 3.1 通用可选参数 `demote`

两个模式均支持表单字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `demote` | bool | 否 | 传 `true` 开启**降误检**：MinerU 判为标题、无编号、长度 > 60 字符且以中文句末标点（`。！？`）结尾的"伪标题"降回正文段落。默认 `false`。语义与 9300 完全一致（同一 normalize/render 链） |

---

## 4. zip 模式（推荐）

### 4.1 请求

```
POST /parse HTTP/1.1
Content-Type: multipart/form-data
```

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件（binary） | 是* | 待解析文件二进制 |
| `demote` | bool | 否 | 见 §3.1 |

> \* **务必设置带扩展名的文件名**（multipart filename）。本服务的格式前置校验按扩展名判定（§6，大小写不敏感）；缺文件名或无扩展名的上传一律 `400`。

### 4.2 响应

**成功**：`200`，`Content-Type: application/zip`，body 为 zip 二进制：

```
result.md              ← 全文 Markdown（UTF-8 无 BOM）
images/xxx.jpg         ← 提取图片（有图时）
```

**失败**：非 2xx。zip 模式的错误体为 FastAPI 默认结构 `{"detail": "..."}`，见 §7。

### 4.3 curl 示例

```bash
curl -X POST "http://128.23.67.112:9302/parse" \
  -F "file=@/path/to/report.pdf" \
  -o result.zip

# 开启降误检
curl -X POST "http://128.23.67.112:9302/parse" \
  -F "file=@/path/to/scan.pdf" \
  -F "demote=true" \
  -o result.zip
```

### 4.4 产物结构

```
result.zip                ← 契约约定形态；bellm 实际布局待实测确认
├── result.md
└── images/             ← 有图时存在
    └── ab12cd34.jpg
```

- result.md 中图片引用为相对路径：`![x](images/ab12cd34.jpg)`，与 zip 内目录一致——以上均为**契约约定形态**。bellm 引擎的实际图片引用格式与 zip 内落盘布局**尚待带插图 PDF 实测定案**（两份实测样本 md 均无图片引用；服务端按图片键名平铺落盘，zip 模式下文件可能落 zip 根而非 `images/` 子目录，见《HPC112对外接口明细调查》§8.4）。定案前调用方**不应假设解压后即可直接渲染**。

---

## 5. 路径模式（共享文件系统时）

> 要求调用方与本服务**共享同一个文件系统**，且服务进程能读写这些路径（容器需挂载对应目录，与 9300 的部署要求相同）。

### 5.1 请求

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | 字符串 | 是 | 待解析文件**绝对路径**（服务端本地） |
| `output_dir` | 字符串 | 是 | 产物目录**绝对路径**，服务写入 `{output_dir}/result.md`；**缺失 → 400** |
| `image_dir` | 字符串 | 否 | 图片目录**绝对路径**；缺省落 `{output_dir}/images`（建议显式传 `output_dir/images`） |
| `demote` | bool | 否 | 见 §3.1 |

### 5.2 响应

**成功**：`200` + `{"status": "ok"}`

**失败**：非 2xx + `{"status": "error", "message": "..."}`，见 §7。

### 5.3 curl 示例

```bash
curl -X POST "http://128.23.67.112:9302/parse" \
  -F "file_path=/data/chai/upload/20261030/abc.pdf" \
  -F "output_dir=/data/chai/upload/20261030/abc_out" \
  -F "image_dir=/data/chai/upload/20261030/abc_out/images"
```

---

## 6. 支持的文件类型（bellm 引擎硬限制）

### 6.1 格式实测矩阵（2026-10-28，网关直测）

| 扩展名 | 网关实测 | 本服务行为 |
|---|---|---|
| `.pdf` `.png` `.jpg` `.jpeg` | 200，正常解析 | 放行 |
| `.bmp` `.tif` `.md` `.xlsx` `.docx` 等 | 500 玄学报错 | **前置拦截 400**（不浪费一次网关调用） |

- 判定依据：multipart `filename`（zip 模式）或 `file_path`（路径模式）的**扩展名**，**大小写不敏感**（`.PDF` 视同 `.pdf`）
- 两种模式统一拦截；**无文件名 / 无扩展名同样 400**（不 sniff 魔数）
- 拦截响应体（两种模式一致）：

```json
{"status": "error", "message": "研发中心通道(bellm)仅支持 PDF/PNG/JPG/JPEG，收到: 报告.docx"}
```

### 6.2 性能基线（2026-10-28 实测，网关侧）

| 样本 | 耗时 |
|---|---|
| 42 页 PDF | 28s（约 0.7s/页） |
| 21KB 扫描件 | 1.3–1.5s |

> 大文档耗时按 ~0.7s/页 估算；超过服务内部 300s 网关超时（约合 400+ 页）会以 `502` 返回，建议超大文档分批。

---

## 7. 错误响应

错误响应体有两种形态（沿 9300 既有实现，非本服务新引入）：

| 形态 | 出现场景 |
|---|---|
| `{"status": "error", "message": "..."}` | bellm 前置格式校验（§6）、**路径模式**的全部错误 |
| `{"detail": "..."}`（FastAPI 默认） | **zip 模式**解析期错误（空文件 / 502 / 500）与缺字段 400 |

| 状态码 | 触发 | 响应体示例 |
|---|---|---|
| `400` | 格式前置校验不过（非 PDF/PNG/JPG/JPEG、无文件名、无扩展名） | `{"status":"error","message":"研发中心通道(bellm)仅支持 PDF/PNG/JPG/JPEG，收到: 报告.docx"}` |
| `400` | zip / 路径两模式字段全缺 | `{"detail":"缺少 file（zip模式）或 file_path（路径模式）"}` |
| `400` | 路径模式缺 `output_dir` | `{"status":"error","message":"路径模式需 output_dir"}` |
| `400` | 路径模式文件不存在 | `{"status":"error","message":"文件不存在: /data/xxx.pdf"}` |
| `400` | zip 模式空文件（0 字节） | `{"detail":"空文件"}` |
| `500` | 未预期异常 | zip `{"detail":"<异常类型>: <消息>"}` / 路径 `{"status":"error","message":"<异常类型>: <消息>"}` |
| `502` | bellm 网关不可达 / 非 200（含网关侧解析错误、**凭证失效 401**、超 300s 超时） | zip `{"detail":"MinerU 调用失败：bellm 返回 401：…"}` / 路径 `{"status":"error","message":"MinerU 调用失败：…"}` |
| `401` | **无此码**——本服务无鉴权；网关侧 401 已被服务适配为 `502` | — |

**重试建议**：`400` 为调用方错误，改请求后重发（重试无意义）；`500` 可安全重试（幂等）；`502` 先区分原因——网关抖动可重试，凭证失效（message 含 `401`）需服务端换凭证，调用方重试无效。

> 另有 FastAPI 自动产生的 `422`（请求不符合参数声明，如非 multipart 编码），属调用方式错误，正常 multipart 调用不会触发。

---

## 8. 调用方检查清单

- [ ] multipart `filename` 带正确扩展名，且在 `.pdf/.png/.jpg/.jpeg` 白名单内（大小写不敏感；其余一律 400）
- [ ] 读超时 ≥ 6 分钟（覆盖服务内部 300s 网关超时）
- [ ] 无需任何凭证/请求头（服务无鉴权；网关凭证在服务端）
- [ ] zip 模式：`file` 字段为文件本身（非 base64 字符串）
- [ ] 路径模式：`file_path` + `output_dir` 必填（`image_dir` 可省，缺省落 `{output_dir}/images`），路径需在容器挂载范围内
- [ ] 失败重试前先读错误体：400 类改请求；502 含 `401` 找服务端；其余可安全重试

---

## 9. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-30 | 文档随 bellm 引擎（mineru2doc commit `e0a64c6`）成册；容器 `mineru2doc-bellm` :9302 待 112 部署验收 |
