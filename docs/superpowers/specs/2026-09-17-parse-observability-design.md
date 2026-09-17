# /parse-pdf 可观测性设计：文件名日志 + 分阶段耗时记录

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-09-17 |
| 状态 | 已与用户确认设计，待实现 |
| 关联 | 2026-09-16 112 楔死事故（楔死请求在日志中零痕迹，取证被迫 py-spy + 抓包） |

---

## 1. 背景与动机

当前 `/parse-pdf` 的日志只有 uvicorn access log，且**仅在请求完成时**输出一行、不含文件名。2026-09-16 事故中一个病态 PDF 楔死服务 5 小时，期间日志完全静默，无法回答「哪个文件、多大、几点来的」——最终靠 py-spy + tcpdump 抓包才定位。同时，排查「慢解析慢在哪个阶段」无任何数据支撑。

## 2. 目标

1. **请求到达即记录**：解析开始前，日志中出现 request_id、文件名、大小、来源 IP、调用模式
2. **分阶段耗时**：每个解析阶段的耗时（人读 + 结构化两路输出）
3. **OCR 逐页耗时**：每次远程 OCR 调用一行（最慢环节）
4. **失败也有记录**：异常/被杀的请求留下到达行与已完成阶段，事后可对账

### 非目标（明确排除）

- 可编辑 PDF 的逐页计时（速度快、价值低）
- OpenTelemetry / tracing 框架 / metrics 端点
- uvicorn access log 格式改造
- 解析移线程池 / 子进程化超时加固（**独立事项**，见备忘 mupdf-poison-pdf-incident）
- Chai 对接侧任何改动

## 3. 架构（方案 A：轻量计时模块 + serve 层显式打点）

```
api.py (_chai_parse)          ← 到达行 + 创建 StageTimer(request_id, file, size, mode, from_ip)
        │ timer 作为可选参数传入
serve.py (parse_to_zip/files) ← 6 阶段打点: detect/extract/assemble/demote/render/pack
        │ 设置 current_timer 上下文
ocr/extractor.py 页循环       ← 每页 ocr_page 行 + 页耗时回填 timer
        │
timing.py (新模块)            ← StageTimer + JsonlWriter(按天滚动) + contextvar
```

### 3.1 新模块 `document2chunk/timing.py`

- **`StageTimer`**：`mark(stage)` 逐阶段记耗时；`ocr_page(i, elapsed, total)` 记页级；`finish(status, error_type=None)` 产出汇总并双路输出；属性 `request_id / file / size_bytes / mode / source_type / demote` 由创建方注入
- **`JsonlWriter`**：模块级单例 + `threading.Lock`；按天滚动文件名 `timing-YYYYMMDD.jsonl`；每次写入 open-append-close（无长持句柄）；写盘失败 `logger.warning` 并自动降级为仅 stdout（进程内标记，不反复重试刷屏）
- **`current_timer`**：`contextvars.ContextVar`，serve 在调用 extractor 前设置；extractor 通过 `timing.get_current_timer()` 取，无则全部 no-op（保持旧调用方零影响）

### 3.2 `api.py` 改动（`_chai_parse`）

- form 解析完成后（拿到 filename/file_path）、调用 `serve` 之前：生成 `request_id = uuid4().hex[:8]`，打**到达行**并创建 `StageTimer`
- 文件名：zip 模式取 multipart filename；路径模式取 `file_path` basename；`size_bytes` zip 模式取上传字节数、路径模式取 `os.path.getsize`（**stat 失败记 null，不阻断 ARRIVE**——文件不存在的 400 场景也要有到达行）
- 异常路径：`finish("error", error_type=...)`，detail 照旧返回
- `/parse`（mineru2doc 代理）与 `/parse-json` **不在本次范围**

### 3.3 `serve.py` 改动

- `parse_to_zip` / `parse_to_files` 增加可选参数 `timer: Optional[StageTimer] = None`；为 None 时**内部自建**（CLI 路径自动获得全套记录）
- 阶段打点（`try/finally` 包裹，异常路径也能记录已完成阶段）：

| 阶段 | 覆盖内容 |
|---|---|
| `detect` | `_route_source_type`（扩展名/魔数路由 + pdf_detect 类型判定） |
| `extract` | `_extract_with_images`（PDF/DOCX/OCR 引擎，黑盒） |
| `assemble` | `_assemble`（结构组装 + 统一后处理） |
| `demote` | `_apply_demote`（仅 demote=true 时非零） |
| `render` | `_doc_markdown`（全文 markdown 渲染） |
| `pack` | zip 打包 / result.md 写盘（按模式） |

- `.md`/`.txt` 早退路径：`finish("passthrough")`，stages 可为空
- `source_type` 路由结果回填 timer

### 3.4 `ocr/extractor.py` 改动

- 页循环内 `client.parse` 前后计时；每页打 stdout 行 `[req=xx] ocr_page page=i/N elapsed=x.xxs`
- 页耗时数组经 `current_timer` 回填（JSONL `ocr_pages_s` 字段）
- 无 timer 时行为不变（仅跳过记录）

## 4. 输出样例

stdout（人读，恒开）：

```
[req=3f2a91c4] ARRIVE file="report.converted.pdf" size=7250492B mode=zip from=128.23.74.3
[req=3f2a91c4] ocr_page page=3/52 elapsed=4.21s
[req=3f2a91c4] DONE status=ok total=38.2s detect=0.4s extract=31.1s assemble=2.1s demote=0.0s render=3.2s pack=1.4s
```

JSONL（`timing-YYYYMMDD.jsonl`，每请求一行）：

```json
{"ts":"2026-09-17T10:00:00+08:00","request_id":"3f2a91c4","file":"report.converted.pdf","size_bytes":7250492,"mode":"zip","source_type":"pdf","demote":false,"status":"ok","total_s":38.2,"stages":{"detect":0.4,"extract":31.1,"assemble":2.1,"demote":0.0,"render":3.2,"pack":1.4},"ocr_pages_s":[1.2,4.21]}
```

- `status` ∈ `ok | error | passthrough`
- error 行额外含 `"error_type":"InvalidPdfError"`（异常类名）与已完成 stages
- **楔死场景**：只有 ARRIVE 行、无 DONE 行——重启后按 request_id 对账即知元凶（与 save_dir 留痕互补，但零字节落盘成本）

## 5. 配置与部署

| 项 | 值 |
|---|---|
| `DOCUMENT2CHUNK_LOG_DIR` | JSONL 目录，默认 `<cwd>/logs`；Docker 内 `/app/logs` |
| docker-compose.d2c.yml | 增挂卷 `./d2c_logs:/app/logs` |
| 开关 | 无（恒开；日志量 ≈ 每请求 2-3 行 + OCR 页数行，开销可忽略） |

## 6. 测试（TDD）

1. **StageTimer 单测**：阶段顺序累计、异常路径部分记录、finish 汇总字段、passthrough
2. **JsonlWriter 单测**：按天文件名、写盘失败降级仅 stdout、并发写加锁
3. **serve 集成测**（mock extractor，沿用 test_serve.py 既有模式）：
   - zip 模式正常流：JSONL 行字段齐全、ARRIVE 行先于 DONE 行（同一 caplog 顺序）
   - 路径模式：file 取 basename、size 取磁盘文件
   - 解析抛异常：`status=error` + `error_type` + 部分 stages
   - `.md` 直通 / `.txt` 转录：`status=passthrough`
4. **OCR 页级**：mock client 两页 fixture → 两条 `ocr_page` 行 + `ocr_pages_s` 长度 2
5. **CLI**：`cli_main` 跑通后 logs 目录出现 JSONL 行

## 7. 验收

- 真实请求后 `docker logs` 可见 ARRIVE/ocr_page/DONE 三类行；`jq .stages` 可分析
- 112 部署后用普通 PDF 跑一次，`/app/logs/timing-*.jsonl` 出现合法 JSON 行
