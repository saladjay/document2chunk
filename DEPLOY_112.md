# 部署 Document2Chunk /parse-pdf 到 128.23.67.112

> /parse-pdf 接口 + CLI 已实现（`src/document2chunk/serve.py`、`api.py` 的 `/parse-pdf` 端点、`__main__.py`）。
> 我无法 SSH 到 112（无密钥），以下命令需你在 112 上执行（`ssh hpc`）。

## 现状

- **112:9300** 现跑 **mineru2doc**（`/parse`，mineru-adapter），**无 `/parse-pdf`**。
- 本仓库新增 `Dockerfile.d2c` + `docker-compose.d2c.yml`：Document2Chunk 服务（`/parse` + `/parse-pdf`）。

## 端口决策

`:9300` 已被 mineru2doc 占用。两种方案：

- **方案 A（推荐，简单）**：d2c 部署在 **:9301**，Chai 的 `chai.mineru.base-url = http://128.23.67.112:9301/parse-pdf`。
- **方案 B（同 :9300）**：加 nginx 反向代理，:9300 路由 `/parse`→mineru2doc、`/parse-pdf`→d2c。复杂，非必需。

下面按**方案 A**。

## 部署步骤（在 112 上，ssh hpc）

```bash
# 1. 拉最新代码（仓库已在 112 上，mineru2doc 也从此构建）
cd /path/to/document2chunk     # 改成 112 上仓库实际路径
git pull origin main

# 2. 准备 OCR token（PaddleOCR 服务 token，同 :8000 那个）
export DOCUMENT2CHUNK_OCR_TOKEN=06mPxXt3BEhP6cM6DGlZ9EPdVeUP1ULo1cM5vfuloi8
#   或写 .env：echo "DOCUMENT2CHUNK_OCR_TOKEN=..." > .env

# 3. 构建并启动 d2c 服务（:9301）
docker compose -f docker-compose.d2c.yml up -d --build

# 4. 看日志确认启动
docker compose -f docker-compose.d2c.yml logs -f document2chunk
```

## 验证

```bash
# health
curl http://128.23.67.112:9301/health
# 期望：{"status":"ok","version":"0.1.0"}

# zip 模式自测（edited PDF，快）
curl -X POST http://128.23.67.112:9301/parse-pdf \
  -F "file=@/path/to/test.pdf" -o /tmp/r.zip
python -c "import zipfile; z=zipfile.ZipFile('/tmp/r.zip'); print(z.namelist()[:4]); print(z.read('result.md').decode()[:60])"

# 路径模式自测
curl -X POST http://128.23.67.112:9301/parse-pdf \
  -F "file_path=/path/to/test.pdf" \
  -F "output_dir=/tmp/test_out" \
  -F "image_dir=/tmp/test_out/images"
cat /tmp/test_out/result.md | head
ls /tmp/test_out/images/
```

## Chai 配置（对接方）

```yaml
chai:
  mineru:
    mode: http
    base-url: http://128.23.67.112:9301/parse-pdf   # d2c 服务
    output-path: false        # zip 模式（跨机器）；同机共享盘可 true 走路径模式
    image-dir: images
    demote: false             # 误判长句标题降正文（可选）
```

## CLI 模式（如 d2c 与 Chai 同机）

```yaml
chai:
  mineru:
    mode: cli
    cli-command: "docker exec document2chunk python -m document2chunk cli --input {inputFile} --output {outputDir} --images {imageDir}"
    # 或宿主直装：python -m document2chunk cli --input {inputFile} --output {outputDir} --images {imageDir}
```

## OCR 说明

- d2c 的 OCR 路径走宿主 **PaddleOCR :8000**（`DOCUMENT2CHUNK_OCR_ENDPOINT=http://host.docker.internal:8000`）。
- 扫描件经 OCR；可编辑 PDF 走 PyMuPDF（无需 OCR）。
- OCR 模型默认 `vl`；如 vl 因 GPU 占用 500，切 `pp-ocrv6`（`DOCUMENT2CHUNK_OCR_MODEL=pp-ocrv6`，但格式适配待补，见 designs/010）。
