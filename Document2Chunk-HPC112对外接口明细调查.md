# Document2Chunk HPC(112) 对外接口明细调查

| 项 | 内容 |
|---|---|
| 调查日期 | 2026-10-26 |
| 调查对象 | 本仓库（document2chunk）部署在 HPC 服务器 `128.23.67.112` 上的对外 HTTP 接口 |
| 调查方法 | 宿主机 `ss -tlnp` / `docker ps` / `docker inspect` 实测 + 两容器 `/openapi.json` 拉取 + 本地 main 源码（`07a9798`）逐端点核对 + 本机外网连通性实测 |
| 文档性质 | 运维/对接视角的**事实盘点**（部署了什么、暴露了什么、行为是什么）；调用方对接细节见 §6 文档索引 |
| 范围声明 | 只覆盖**本仓库**的两个服务；同主机还有大量无关服务（vllm、maxkb、milvus、gotenberg、minio 等），不在本文范围 |

---

## 1. 部署总览

本仓库在 112 上以 **两个容器** 对外提供服务，均监听 `0.0.0.0`（外网/内网直连可达）：

| 容器 | 对外端口 | 容器内端口 | 代码位置 | 部署版本 | 镜像构建时间 | 状态 / 资源限额 |
|---|---|---|---|---|---|---|
| `document2chunk` | **9301** | 8000 | `src/document2chunk/` | main@`07a9798`（与本地 main 一致） | 2026-09-22 12:01 | healthy；4 CPU / 4G |
| `mineru2doc` | **9300** | 8000 | `mineru2doc/`（仓库子目录） | 镜像 2026-07-24；之后 main 仅多 1 个 docs commit（82291db README）→ **运行代码即 main** | 2026-07-24 11:46 | healthy；2 CPU / 2G |

- compose 文件同在 `/home/user/document2chunk/`：
  - `docker-compose.d2c.yml` → document2chunk（9301，`Dockerfile.d2c`，装 `.[pdf,ocr,docx,api,viz]`）
  - `docker-compose.yml` → mineru2doc（9300）
- **外部可达性实测**（本机 Windows 直连）：`GET 9301/health` 200、`GET 9301/openapi.json` 200、`GET 9300/health` 200。
- 健康检查：两容器均为 30s 间隔 urllib 探测容器内 `/health`，5s 超时，3 次重试。

### 1.1 调用链依赖

```
调用方(Chai 128.23.74.3, retry≤3)
   │
   ├─→ 9301 /parse        ──代理──→ 9300 /parse (mineru2doc)
   ├─→ 9301 /parse-pdf     d2c 引擎 ──扫描件──→ 宿主 pandocr :8000 (OCR, 有 token)
   │                                 └─表格──→ 表格识别(经 pandocr 代理)
   └─→ 9300 /parse (mineru2doc) ──────→ 宿主 MinerU :9030 (mineru-api)
```

容器经 `host.docker.internal`（`extra_hosts: host-gateway`）访问宿主服务。

### 1.2 容器环境变量（实测 `docker inspect`）

**document2chunk（9301）**：

| 变量 | 值 | 说明 |
|---|---|---|
| `DOCUMENT2CHUNK_OCR_ENDPOINT` | `http://host.docker.internal:8000` | 宿主 pandocr |
| `DOCUMENT2CHUNK_OCR_TOKEN` | `***`（值已隐去；表格服务 token 同源） | .env 注入；缺失→扫描件 500 |
| `DOCUMENT2CHUNK_OCR_MODEL` | `vl` | |
| `DOCUMENT2CHUNK_OCR_TIMEOUT` | `300` | |
| `DOCUMENT2CHUNK_SAVE_OUTPUT_DIR` | （空） | zip 模式响应留痕**关闭**；写 `/app/outputs` 可重开 |
| `DOCUMENT2CHUNK_DOC_TARGET` / `DOCUMENT2CHUNK_SOFFICE_TIMEOUT` | `docx` / `120` | 老格式归一化 |

**mineru2doc（9300）**：`MINERU_BASE_URL=http://host.docker.internal:9030`、`MINERU_TIMEOUT=300`。

---

## 2. document2chunk :9301（6 个端点）

OpenAPI（`/openapi.json`）标题 `document2chunk`，version `0.1.0`。源码：`src/document2chunk/api.py`。

| 方法 | 路径 | api.py | 引擎 | 用途 |
|---|---|---|---|---|
| GET | `/health` | :409 | — | 探活 → `{"status":"ok","version":"0.1.0"}` |
| POST | `/parse` | :525 | **MinerU** | 代理到 ：9300（见 §2.4） |
| POST | `/parse-pdf` | :549 | d2c | 文档解析主入口（见 §2.1） |
| POST | `/parse-doc` | :553 | d2c | `/parse-pdf` **完全别名**（同 handler，2026-09-20 新增） |
| POST | `/parse-excel` | :558 | d2c-excel | Excel/csv → 行级 JSON envelope（见 §2.2） |
| POST | `/parse-json` | :581 | d2c | 库 API：上传 → `{document: IR, markdown}`（见 §2.3） |

### 2.1 `/parse-pdf`（别名 `/parse-doc`）——Chai 契约，双模式

`_chai_parse`（api.py:413）要求 `multipart/form-data`，按字段自动判模式：

| 模式 | 触发 | 请求字段 | 响应 |
|---|---|---|---|
| **zip 模式** | 有 `file`（或 `document`），无 `file_path` | `file` + 可选 `demote` | `application/zip` 流式（`result.zip`：根目录 `result.md` + `images/`；UTF-8 无 BOM，图片相对路径引用） |
| **路径模式** | 有 `file_path`（优先级高于 `file`） | `file_path` + `output_dir` + `image_dir`（缺一 400）+ 可选 `demote` | `{"status":"ok"}`；服务端直接写 `output_dir/result.md`、图片写 `image_dir` |

两者共用：

- **`demote`**（默认 false）：标题文本 >60 字符且以句号结尾 → 降为正文（`serve.py:39` `_DEMOTE_MAX_HEADING_LEN`）
- **同步阻塞**，无任务 ID/轮询；幂等可重试
- **支持格式**：
  - PDF：editable/scanned 自动路由（`pdf_detect` 判定 + 毒 PDF 看门狗：detect 120s、extract 600s 子进程超时，env `DOCUMENT2CHUNK_DETECT_TIMEOUT` / `DOCUMENT2CHUNK_EXTRACT_TIMEOUT`）
  - DOCX、扫描图片（`.png .jpg .jpeg .bmp .tif .tiff .gif .webp`）、`.md`/`.txt`
  - 老格式 `.doc .rtf .wps .ppt .pptx` → soffice 归一化为 docx（指纹识别）
  - **表格类 `.xls .xlsx .xlsm .et` → 400**（明确二期，应走 `/parse-excel`）
- **并发控制**：解析信号量默认 2（env `DOCUMENT2CHUNK_PARSE_CONCURRENCY`），防止大文档并发 OOM
- **错误码**：

| 码 | 触发 |
|---|---|
| 400 | 非 multipart / 缺字段 / 文件不存在 / 不支持格式（UnsupportedFormatError） |
| 422 | InvalidPdfError / InvalidDocxError 等文档级错误 |
| 500 | 未预期异常（`{type}:{msg}`） |
| 503 | 依赖缺失（MissingDependencyError） |
| 502 | 仅 `/parse`：mineru2doc 不可达 |

- **可观测性**：ARRIVE 行（request_id/文件名/大小/来源 IP/mode/demote）+ StageTimer 全程计时 → 宿主 `d2c_logs/timing-YYYYMMDD.jsonl`（uvicorn 日志无时间戳，查此文件）
- **zip 模式内存防线**：上传 1MB 分块流式落盘、zip 直写文件、FileResponse 发完后台删除临时文件

### 2.2 `/parse-excel` ——同步 JSON，无 zip 模式

multipart 字段 `file`（或 `file_path` 服务端路径）→ **直接返回 JSON envelope**（轨A chunk2embedding 契约，`serve.py:224` + `extractors/excel/serializer.py:13`）：

```json
{
  "file": "...",
  "warnings": ["..."],
  "rows":   [{"sheet": "...", "row": 1, "data": [...], "text": "...", "meta": {...}}, ...],
  "blocks": [{"sheet": "...", "kind": "...", "text": "...", "meta": {...}}, ...],
  "sheets": [...]
}
```

格式路由：`.csv` 轻量路径；XLS 指纹（含 `.xls` 冒充 `.xlsx`）与 `.et` 非 xlsx 内容 → soffice 转 xlsx；其余非 XLSX 内容 → 400。

### 2.3 `/parse-json` ——库 API（旧 /parse 行为）

- query 参数：`source_type`（pdf/docx/ocr，缺省按扩展名+魔数自动路由）、`keep_toc`、`extract_images`
- body：multipart `file`/`document`，或原始二进制（`?filename=` 提示扩展名）
- 响应：`{"document": <LogicalDocument IR>, "markdown": "<全文 md>"}`；已剔除 `ImageNode.data` 原始字节（图片走 image_id + 落盘），响应不含 base64
- 注意：`ParseOptions`（dpi=150、`table_complex_format` html/image、`table_image_dpi`、deskew、extract_tables）**未从 HTTP 暴露**，走库内默认（api.py:106）

### 2.4 `/parse` ——mineru2doc 代理（api.py:525）

- 整包读 body → 透传 multipart（剔除 host/content-length/transfer-encoding 头）→ `http://host.docker.internal:9300/parse`，httpx 超时 **600s**
- 回传 mineru2doc 的原始状态码与 content-type（zip 流或 `{"status":"ok"}`）；mineru2doc 不可达 → 502

---

## 3. mineru2doc :9300（2 个端点）

OpenAPI 标题 `mineru2doc`，version `0.1.0`。源码：`mineru2doc/server.py`（对接 Chai「MinerU-Adapter」契约 v2.0）。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{"status":"ok","version":"0.1.0","mineru_base_url":"http://host.docker.internal:9030"}` |
| POST | `/parse` | 5 个表单字段：`file`（zip 模式）/ `file_path`+`output_dir`+`image_dir`（路径模式，需 output_dir）/ `demote`（bool，默认 false）；两者都没有 → 400 |

- 与 9301 `/parse-pdf` 契约对齐（同双模式、同 demote 语义），但**引擎不同**：本服务把文档送宿主 MinerU `:9030` 解析为多级标题 Markdown，再 normalize/render
- 错误语义（mineru2doc 自有，区别于 d2c）：失败返回 **非 2xx + `{"status":"error","message":...}`**；`MinerULoaderError`（MinerU 调用失败）→ **502**；其余异常 → 500；空文件/缺字段 → 400
- 每次解析一行结构化日志：`file/ms/headings/levels/images` 统计
- zip 模式响应：`application/zip`，`Content-Disposition: attachment; filename="result.zip"`，内容 `result.md` + `images/`

### 3.1 d2c 与 mineru2doc 契约差异速查

| 维度 | document2chunk :9301 | mineru2doc :9300 |
|---|---|---|
| `/parse` | 代理转发到 9300 | 本体实现 |
| `/parse-pdf` `/parse-doc` `/parse-excel` `/parse-json` | 有 | 无 |
| 失败响应体 | FastAPI `{"detail": ...}` | `{"status":"error","message":...}` |
| 文档级错误码 | 422（InvalidPdf/InvalidDocx） | 500（无 422 分支） |
| 支持格式 | pdf/docx/图片/md/txt/老格式归一化 | 由宿主 MinerU 决定（pdf 为主） |
| 毒 PDF 防线 | detect 120s / extract 600s 子进程看门狗 | 依赖 MINERU_TIMEOUT=300 |

---

## 4. 鉴权与暴露面（安全观察）

1. **两个服务均无鉴权**（无 token、无 IP 白名单），且 `0.0.0.0` 对外。对照组：同主机 pandocr `:8000`、表格识别服务均有 token。路径模式还能让调用方让服务读**容器内任意路径**文件、写任意 output_dir——只应在可信内网提供。
2. 当前访问面：本机（外网侧）直连 9300/9301 均 200，未发现防火墙拦截。

---

## 5. 版本一致性核查（2026-10-26）

| 检查 | 结果 |
|---|---|
| 服务器 main vs 本地 main | 同为 `07a9798`（服务器 `ahead 27 of origin/main`，与 gitea/main 同步——112 只能从 gitea 拉） |
| d2c 镜像(09-22) 之后 main 的 commit | 3 个，全部是 `_embedlab`/docs，**不影响 runtime** |
| mineru2doc 镜像(07-24) 之后 mineru2doc/ 的 commit | 仅 1 个（82291db README），**不影响 runtime** |
| `git diff 07a9798..HEAD -- src/ mineru2doc/` | 空 → 部署 runtime 代码 = 本地 main |

---

## 6. 文档索引（既有，未重写）

| 文档 | 性质 |
|---|---|
| `parse-pdf接口调用文档.md`（main 已跟踪） | 调用方视角接口参考，与实测行为一致 |
| `parse-doc接口调用文档.md`（main 已跟踪） | 同上（别名独立成册） |
| `MinerU-Adapter接口对接需求文档.md`（工作区未跟踪） | Chai 对接需求（:9300 契约 v2.0） |
| `Document2Chunk-PDF解析接口对接需求文档.md`（工作区未跟踪） | PDF 解析接口对接需求 |
| `mineru2doc/README.md`（82291db） | mineru2doc 输出结构 |
| `DEPLOY_112.md` | 112 部署手册 |

## 7. 遗留建议（不阻塞）

- 9300/9301 若需长期对外，建议补 token 或收敛到 nginx 网关后面（同主机已有 nginx:80/6060/8086/9020/9502 可挂）。
- `/parse-json` 如需暴露 `ParseOptions`（表格 html/image 切换等），需加 query/form 参数——当前只有库内默认值。
- mineru2doc 镜像已 3 个月未重建；下次改 mineru2doc/ 代码时记得 `docker compose -f docker-compose.yml up -d --build`。

## 8. mineru2doc-bellm（9302）——研发中心通道（2026-10-30 增）

对应需求方（Chai 侧）命名的 **ParserDocByRDMinerU**（RD = 研发中心，即网关运营方）：同一 `mineru2doc:latest` 镜像的第二个容器，`MINERU_ENGINE=bellm`，把文档送研发中心 bellm 网关（`http://128.23.74.3:9091/bellm/MinerU/file_parse`，容器直连）解析。对接文档独立成册：`ParserDocByRDMinerU接口对接文档.md`。

### 8.1 设计共识（计划 `docs/superpowers/plans/2026-10-30-parserdoc-by-rdmineru.md`）

| 维度 | 决策 |
|---|---|
| 架构 | 引擎差异全部收敛在 loader 接缝：新增 `BellmLoader`（`file` 单数字段 + `parse_method=auto` + `Authorization` 头 + data-url 前缀 base64 图片剥前缀落盘）；md→RegexJudge→normalize→render 全链与 9300 共用 |
| 引擎开关 | env `MINERU_ENGINE=local\|bellm`，默认 `local`——9300 现网行为零改动 |
| 契约 | `/parse` 五字段与 9300 逐字段一致（`file` / `file_path`+`output_dir`+`image_dir` / `demote`）；仅新增 bellm 前置格式校验 |
| 凭证 | `MINERU_BELLM_AUTH`（'Basic xxx'）走 112 `.env` → compose 注入，不进代码库；engine=bellm 且缺失 auth 时 `create_app` 抛 `RuntimeError`（容器启动即退，fail-fast） |
| 现网影响 | 9300 / 9301 容器与代码路径不改动；build 刷新 `mineru2doc:latest` 后 9300 重启才换新（默认引擎未变，行为等价） |

bellm 引擎 env（compose 服务 `mineru2doc-bellm`，端口 `9302:8000`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `MINERU_ENGINE` | `local` | `bellm` 时启用本通道 |
| `MINERU_BELLM_BASE_URL` | `http://128.23.74.3:9091/bellm/MinerU` | 网关地址 |
| `MINERU_BELLM_AUTH` | 无 | Basic 凭证；缺失即 fail-fast |

### 8.2 实测矩阵（2026-10-28，网关直测）

| 项 | 结果 |
|---|---|
| 支持格式 | 仅 `.pdf/.png/.jpg/.jpeg` 返回 200；`.bmp/.tif/.md/.xlsx/.docx` 网关侧 500 玄学报错 → 服务前置校验 400（`研发中心通道(bellm)仅支持 PDF/PNG/JPG/JPEG，收到: <name>`，大小写不敏感，两种模式统一） |
| 性能 | 42 页 PDF 28s（约 0.7s/页）；21KB 扫描件 1.3–1.5s |
| 图片 | 响应 `images` 值带 `data:image/...;base64,` 前缀（与 9030 裸 base64 不同），键即文件名；服务剥前缀落盘，调用方拿到的是 zip/目录里的图片文件 |
| 认证 | 网关 Basic 认证 401 门严格；该凭证是**服务端→网关**的内部凭证，调用方无需感知 |

### 8.3 与既有服务的关系

- `/health` 增加 `engine` 字段（`local`/`bellm`）；`mineru_base_url` 为历史字段，仍显示本地引擎地址，不随引擎切换
- 错误码沿用 9300 语义：400（格式前置校验/缺字段/文件不存在）、500（未预期）、502（`MinerULoaderError`：网关不可达/非 200，含 401 与超时）；**无 401**——本服务自身无鉴权，§4 的「无鉴权 + 0.0.0.0」安全观察对 9302 同样适用
- 部署单元在 `docker-compose.yml`（`mineru2doc` 之后）；`.env.example` 追加了凭证占位

### 8.4 截至本节的待办

- 9302 容器尚未在 112 实际部署（部署与验收见计划 Task 6：`docker compose up -d --build mineru2doc-bellm` 后 `health`/zip/格式校验三连验）
- 带插图 PDF 的 md 图片引用格式已定案（Task 5 e2e，2026-10-30 两轮真连验证 refs==saved）：bellm md 引用带前缀 `images/<hash>.jpg`，`_save_images` 按 md 引用的完整相对路径落盘 `images/` 子目录，zip 解压即可渲染——口径同对接文档 §3/§4.4/§9
