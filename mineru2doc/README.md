# mineru2doc 接口协议

> 把文档（PDF/图片/DOCX/PPTX/XLSX）经 **MinerU** 解析后，整理成**多级标题 Markdown** 的解析服务。
> 对接 Chai「MinerU-Adapter」契约 v2.0（HTTP zip / HTTP path / CLI 三模式）。
>
> **部署地址**：`http://128.23.67.112:9300`（Docker 容器 `mineru2doc`，CPU-only，调宿主 MinerU `:9030`）。

---

## 1. 它做什么

上传一个文档 → 调 MinerU 结构化解析 → 用「正则补救 + 相对栈式定级」把标题整理成多级层级 → 输出 Markdown（`#`/`##`/`###` 标题骨架 + 正文 / 表格 / 图片引用）。同步、无状态、单文件进单结果出。

---

## 2. 调用方式总览

| 方式 | 用途 | 入口 |
|---|---|---|
| `GET /health` | 健康检查 | `http://128.23.67.112:9300/health` |
| `POST /parse` **zip 模式** | 跨机器：上传文件二进制，拿回 zip | 表单字段 `file` |
| `POST /parse` **path 模式** | 共享盘：传文件路径，服务直接回写产物 | 表单字段 `file_path` / `output_dir` / `image_dir` |
| CLI `parse` | 命令行调 zip 接口、落盘 result.md | `python -m mineru2doc parse <file>` |
| CLI `cli` | 本地解析写目录（被 Chai `cli-command` 调用） | `python -m mineru2doc cli --input/--output/--images` |

> `/parse` 按**请求里带哪个字段**自动判模式：有 `file_path` → path 模式；否则有 `file` → zip 模式。

---

## 3. HTTP 接口

### 3.1 `GET /health`

```bash
curl http://128.23.67.112:9300/health
```
```json
{"status":"ok","version":"0.1.0","mineru_base_url":"http://host.docker.internal:9030"}
```

### 3.2 `POST /parse` —— zip 模式（默认 / 跨机器）

**请求**：`multipart/form-data`，字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | binary | 是 | 待解析文件二进制（字段名可由调用方 `file-field` 配置，默认 `file`） |
| `demote` | string(`true`) | 否 | 开启"降误检"（把 MinerU 误判的长句标题降为正文），默认关 |

```bash
curl -X POST http://128.23.67.112:9300/parse \
  -F "file=@/path/to/doc.pdf" \
  -o result.zip
```

**成功响应**：`200` + `Content-Type: application/zip`，响应体是**直接 zip 字节流**（非 JSON）。

**失败响应**：非 2xx + JSON，如 `{"detail":"MinerU 调用失败：…"}`。

### 3.3 `POST /parse` —— path 模式（共享文件系统）

> 要求调用方与本服务**共享同一个文件系统**（同一台机或共享盘），且服务进程能读写这些路径。
> 当前 112 容器需挂载对应目录后才可用于生产（见文末"部署注意"）。

**请求**：`multipart/form-data`，字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file_path` | string | 是 | 原始文件**绝对路径** |
| `output_dir` | string | 是 | 产物目录**绝对路径**（服务在此写 `result.md`） |
| `image_dir` | string | 是 | 图片目录**绝对路径**（服务在此写图片，通常 `output_dir/images`） |
| `demote` | string(`true`) | 否 | 开启降误检 |

```bash
curl -X POST http://128.23.67.112:9300/parse \
  -F "file_path=/data/upload/20260724/abc.pdf" \
  -F "output_dir=/data/upload/20260724/abc_out" \
  -F "image_dir=/data/upload/20260724/abc_out/images"
```

**成功响应**：`200` + `application/json`：
```json
{"status":"ok"}
```
服务已把 `result.md` 写入 `output_dir`、图片写入 `image_dir`。

**失败响应**：非 2xx + `application/json`：
```json
{"status":"error","message":"文件不存在: /data/upload/xxx.pdf"}
```

---

## 4. 输出内容结构

### 4.1 zip 模式产物（zip 内结构）

```
result.zip
├── result.md          ← 必须，位于 zip 根目录；UTF-8（无 BOM）
└── images/            ← 可选，提取的图片
    └── <hash>.jpg
```

> `result.md` **必须在 zip 根目录**（条目名就是 `result.md`）。图片放 `images/` 下，`result.md` 用相对路径 `![](images/<hash>.jpg)` 引用。zip 内条目均为相对路径，无 `../`（无 zip-slip）。

### 4.2 path 模式产物（磁盘结构）

```
{output_dir}/
├── result.md          ← 服务写入
└── images/            ← {image_dir}，服务创建并写入
    └── <hash>.jpg
```
`result.md` 内图片用相对路径 `images/<hash>.jpg` 引用（相对 `output_dir`）。

### 4.3 `result.md` 的内容格式

**Markdown 文本**，以 **ATX 标题** 表达多级层级：

```markdown
# 国土资源部文件

## 国土资源部关于改进和优化建设项目用地预审和用地审查的通知

### 一、认真贯彻党中央、国务院决策部署…

为提升效率，制定本方案。…

### 二、简化改进审查内容，切实提高建设用地审批效率

| 列A | 列B |
|-----|-----|
| 1   | 2   |

![流程图](images/b9b969…d14.jpg)
```

要点：

| 项 | 规范 |
|---|---|
| 编码 | UTF-8（无 BOM） |
| 标题 | `#`=H1、`##`=H2、`###`=H3 …（最多 H6）；标题即文档层级骨架 |
| 正文 | 段落原样保留 |
| 表格 | MinerU 给的 HTML/markdown 表格原样保留 |
| 图片 | `![描述](images/<hash>.jpg)` 相对引用 |
| 公式 | 块公式 `$$ latex $$`；列表 `- `/`1. ` |

### 4.4 JSON 响应结构（仅 path 模式 / 失败时）

| 场景 | 结构 |
|---|---|
| path 成功 | `{"status":"ok"}` |
| path 失败 | `{"status":"error","message":"<原因>"}`（HTTP 非 2xx） |
| zip 失败 | FastAPI 默认 `{"detail":"<原因>"}`（HTTP 非 2xx） |

---

## 5. 标题层级是怎么来的（调用方理解用）

1. **MinerU 为主**：信任 MinerU 的标题检出（markdown 的 `#`）。
2. **正则补救**：
   - 修错级：编号标题（`第一章`/`一、`/`（一）`/`1.1`）按编号**相对深度**重新落位层级；
   - 补漏检：正文里漏掉的短编号行（≤40 字、无句尾正文）提升为标题；
   - 降误检（`demote=true` 才开）：MinerU 误判的长句标题降回正文。
3. **相对栈式定级**：首见编号样式落位到上下文层级，更深的嵌套，回到上级则重置——避免把"一、二、三"全压平到 H1。

> 因此同一份文件的标题层级是**稳定可复现**的（幂等）。

---

## 6. 状态码

| 状态码 | 含义 |
|---|---|
| `200` | 成功（zip 模式回 zip；path 模式回 `{"status":"ok"}`） |
| `400` | 请求不合法（空文件 / 缺字段 / path 模式文件不存在） |
| `502` | MinerU 上游失败（服务不可达 / MinerU 拒绝该文件类型） |
| `500` | 服务内部异常 |

---

## 7. 约束

- **同步阻塞**：发请求后等待完整结果，不返回任务 ID 轮询。典型耗时 = MinerU 解析耗时（扫描件 ~20s，长文档更久）。
- **超时**：服务内部 MinerU 超时 `MINERU_TIMEOUT=300s`；调用方建议 ≥30 分钟上限。
- **并发**：单请求线程安全；无需高并发。
- **幂等**：同文件多次解析结果一致。
- **文件类型**：由 MinerU 决定（PDF/图片/DOCX/PPTX/XLSX）；不支持的类型 MinerU 返回 400 → 本服务 502。

---

## 8. CLI（命令行调用）

```bash
# 调 zip 接口、落盘 result.md + images/
python -m mineru2doc parse 文档.pdf -o result.md
python -m mineru2doc parse 文档.pdf -o result.md --service http://128.23.67.112:9300

# 健康检查
python -m mineru2doc health

# CLI 模式（被 Chai cli-command 调用；本机跑、写 output 目录）
python -m mineru2doc cli --input 文档.pdf --output ./out --images ./out/images
#   exit 0 = 成功（已写 out/result.md + out/images/）；非 0 = 失败

# 本地管线（不走服务，本机直连 MinerU 出 markdown）
python -m mineru2doc convert 文档.pdf -o out.md --base-url http://128.23.67.112:9030
```

服务地址默认取环境变量 `MINERU2DOC_URL`，否则 `http://128.23.67.112:9300`，`--service` 可覆盖。CLI 模式的 MinerU 地址取 `MINERU_BASE_URL` 或 `--base-url`。

---

## 9. 部署注意（path 模式生产用）

zip 模式**开箱即用**。path 模式要求服务能读写调用方给的绝对路径——112 容器需挂载共享目录，例如把 Chai 的 upload 目录挂进容器：

```yaml
# docker-compose.yml 的 mineru2doc 服务加：
volumes:
  - /data/chai/upload:/data/chai/upload   # 与 Chai 同路径
```
随后 Chai 传**容器内对应路径**（如 `/data/chai/upload/20260724/abc.pdf`）即可。

---

## 10. 排障

| 现象 | 排查 |
|---|---|
| `502 MinerU 调用失败` | MinerU `:9030` 不可达 / 拒绝文件类型；看 `docker compose logs mineru2doc` |
| path 模式 `文件不存在` | 路径不在容器可见范围（没挂载）/ 编码问题（文件名应为 ASCII/UUID） |
| zip 根目录无 `result.md` | 不会发生（服务固定写根目录）；若发生看日志 `parse-zip fail` |
| 耗时很久 | MinerU 解析大文档；调大调用方超时 |

查每次解析日志：`docker compose logs -f | grep -E "parse-(path|zip) (ok|fail)"`。
