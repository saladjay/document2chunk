# ParserDocByRDMinerU 离线部署文档（从零开始 · 依赖全钉版）

| 项 | 内容 |
|---|---|
| 场景 | 目标服务器**无外网**；依赖在别处（构建机）下载，离线传上服务器部署 |
| 起点假设 | 你手里只有 main 分支的代码（含 `mineru2doc/` 包与 `Dockerfile`），服务器上什么都没有 |
| 产物 | 112 同款服务 `mineru2doc-bellm`（ParserDocByRDMinerU，RD MinerU 通道），对外 9302 |
| 版本策略 | **全部 `==` 精确版本，无通配/无范围**；清单来自 2026-10-30 生产容器（112 `mineru2doc-bellm`）`pip freeze` 实测 |
| 关联 | 《ParserDocByRDMinerU-部署运维文档.md》（在线部署/日常运维）、《ParserDocByRDMinerU接口对接文档.md》（调用方） |

---

## 0. 路线总览（先选路）

| 路线 | 适用 | 服务器上需要 | 推荐度 |
|---|---|---|---|
| **A：整机镜像离线** | 目标机有 Docker（无需联网、无需构建） | Docker + 一个 tar 包 | ⭐ 首选 |
| **B：目标机自建镜像** | 目标机必须自己 build（如安全审计要求） | Docker + 基础镜像 tar + wheels 目录 + 源码 | 备选 |

两条路线**产出的容器完全一致**；区别只是镜像在哪里成型。

---

## 1. 锁定版本清单（2026-10-30 生产实测，全 `==`）

### 1.1 基础镜像

```text
python:3.12-slim-bookworm
digest: sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
内含 Python 3.12.12
```

> 引用基础镜像时**用 digest 而非 tag**（tag 会随官方更新漂移）：`FROM python:3.12-slim-bookworm@sha256:34386ef0...`

### 1.2 Python 依赖（18 个包，生产容器 `pip freeze` 原样）

直接依赖 4 个（Dockerfile 里显式安装的）：

| 包 | 精确版本 |
|---|---|
| httpx | `0.28.1` |
| fastapi | `0.162.0` |
| uvicorn | `0.54.0` |
| python-multipart | `0.0.32` |

传递依赖 14 个（上面 4 个拉进来的，离线安装时**一个都不能少**）：

| 包 | 精确版本 | | 包 | 精确版本 |
|---|---|---|---|---|
| annotated-doc | `0.0.5` | | idna | `3.20` |
| annotated-types | `0.8.0` | | opentelemetry-api | `1.45.2` |
| anyio | `4.15.1` | | pydantic | `2.14.2` |
| certifi | `2026.10.23` | | pydantic_core | `2.50.2` |
| click | `8.5.0` | | starlette | `1.10.0` |
| h11 | `0.16.0` | | typing_extensions | `4.16.0` |
| httpcore | `1.0.9` | | typing-inspection | `0.4.4` |

复验命令（目标机容器起来后跑，应与上表逐行一致）：

```bash
docker exec mineru2doc-bellm python -m pip freeze | sort
```

---

## 2. 路线 A：构建机出整机镜像（推荐）

### 2.1 构建机准备（有网）

```bash
# 取代码（gitea main；或仅拷 mineru2doc/ 目录 + Dockerfile 两个东西）
git clone git@128.23.74.5:dengyongjie/document2chunk.git
cd document2chunk
git checkout main                 # ≥ 37c7df7
```

需要的文件只有两样：`mineru2doc/`（整个目录）+ `Dockerfile`。

### 2.2 构建镜像

```bash
docker build -t mineru2doc:latest .
# 预期：约 2-5 分钟，最终镜像 ~145MB
docker images mineru2doc:latest   # 确认存在
```

### 2.3 导出 + 校验 + 传输

```bash
docker save -o mineru2doc-image.tar mineru2doc:latest
sha256sum mineru2doc-image.tar          # 记下这个值，传完对一下
# 传输（任选）：scp / U盘 / 内部传输入库
scp mineru2doc-image.tar user@<目标机>:/tmp/
```

### 2.4 目标机导入

```bash
sha256sum /tmp/mineru2doc-image.tar     # 与构建机一致
docker load -i /tmp/mineru2doc-image.tar
docker images mineru2doc:latest         # 确认存在
```

到这里的产物：目标机上有了 `mineru2doc:latest` 镜像，**全程零联网**。跳到 §4 启动。

---

## 3. 路线 B：目标机自建镜像（全离线 build）

需要传三个东西上目标机：

| 包 | 怎么来（构建机执行） | 大小参考 |
|---|---|---|
| ① 基础镜像 tar | `docker pull python:3.12-slim-bookworm@sha256:34386ef0...` 然后 `docker save -o py312.tar python:3.12-slim-bookworm` | ~50-130MB |
| ② wheels 目录 | 见下方 `pip download`（锁版 + 锁平台） | 数十 MB |
| ③ 源码 | `mineru2doc/` 目录 + §3.2 的钉版 Dockerfile | <1MB |

### 3.1 wheels 下载（构建机，注意平台参数）

```bash
mkdir wheels
pip download -d wheels/ \
  --platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all: \
  httpx==0.28.1 fastapi==0.162.0 uvicorn==0.54.0 python-multipart==0.0.32
ls wheels/     # 应包含 §1.2 全部 18 个包的 wheel 文件——一个不缺
```

> `--platform/--python-version/--only-binary` 必须带：构建机若是 Windows/macOS，
> 不带会下成目标机装不了的平台包。下载后**逐个核对文件名里的版本号**与 §1.2 一致。

### 3.2 钉版 Dockerfile（整段替换原 Dockerfile；与主仓版仅两处不同：FROM 带 digest、安装全走离线且全 `==`）

```dockerfile
# mineru2doc HTTP API（离线钉版构建）
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

WORKDIR /app

COPY mineru2doc ./mineru2doc
COPY wheels ./wheels

# 全部 == 精确版本 + --no-index：离线、防漂移
RUN pip install --no-index --find-links=/app/wheels \
    httpx==0.28.1 \
    fastapi==0.162.0 \
    uvicorn==0.54.0 \
    python-multipart==0.0.32 \
 && rm -rf /app/wheels

ENV MINERU_BASE_URL=http://host.docker.internal:9030 \
    MINERU_TIMEOUT=300 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "mineru2doc.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 3.3 目标机构建

```bash
docker load -i py312.tar                       # 基础镜像就位
docker build --no-cache -t mineru2doc:latest . # --no-cache：杜绝缓存层混入漂移版本
# 若 install 阶段报找不到某个包：wheels 不全（回构建机核对 §1.2 清单），不允许临时联网补
```

---

## 4. 目标机启动（两台机器通用）

### 4.1 准备凭证

研发中心 Basic 密钥（形如 `Basic <base64>`）写入一个 env 文件：

```bash
mkdir -p /opt/mineru2doc-bellm && cd /opt/mineru2doc-bellm
cat > .env <<'EOF'
MINERU_BELLM_AUTH=Basic <研发中心提供的base64>
EOF
chmod 600 .env
```

> **凭证缺失 = 容器启动即退（fail-fast）**，`docker logs` 会看到
> `RuntimeError: MINERU_ENGINE=bellm 需要 MINERU_BELLM_AUTH`。

### 4.2 启动方式一：docker run（离线首选，不依赖 compose 插件）

```bash
docker run -d \
  --name mineru2doc-bellm \
  --restart unless-stopped \
  -p 9302:8000 \
  --env-file /opt/mineru2doc-bellm/.env \
  -e MINERU_ENGINE=bellm \
  -e MINERU_BELLM_BASE_URL=http://128.23.74.3:9091/bellm/MinerU \
  -e MINERU_TIMEOUT=300 \
  -e PYTHONUNBUFFERED=1 \
  --memory 2g --cpus 2 \
  --health-cmd "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)\"" \
  --health-interval=30s --health-timeout=5s --health-retries=3 --health-start-period=10s \
  mineru2doc:latest
```

### 4.3 启动方式二：compose（目标机有 compose 插件时；**无 build 段**，纯离线）

`/opt/mineru2doc-bellm/docker-compose.yml`：

```yaml
services:
  mineru2doc-bellm:
    container_name: mineru2doc-bellm
    image: mineru2doc:latest          # 离线 load/load 进来的镜像；无 build:
    restart: unless-stopped
    environment:
      - MINERU_ENGINE=bellm
      - MINERU_BELLM_BASE_URL=${MINERU_BELLM_BASE_URL:-http://128.23.74.3:9091/bellm/MinerU}
      - MINERU_BELLM_AUTH=${MINERU_BELLM_AUTH}
    ports:
      - "9302:8000"
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
```

`.env` 放同目录，`docker compose up -d` 即可。

### 4.4 启动确认

```bash
docker ps --filter name=mineru2doc-bellm --format '{{.Status}}'
# → Up X seconds (health: starting) → 约 30s 后 (healthy)
docker logs mineru2doc-bellm --tail 5          # 无 RuntimeError
```

> 网络前提：目标机 → `128.23.74.3:9091`（研发中心网关）可达；目标机无外网不影响本服务（它只调这个内网网关）。

---

## 5. 接口验证（六步，全部有实测基准）

```bash
# ① 探活：必须含 "engine":"bellm"
curl -s -m 10 http://127.0.0.1:9302/health
# 基准：{"status":"ok","version":"0.1.0","mineru_base_url":"...","engine":"bellm"}
# （mineru_base_url 是历史字段，bellm 通道不使用它，恒显 9030 地址——已知行为）

# ② zip 模式 PDF（核心功能）
curl -s -m 300 -o /tmp/acc.zip -w 'HTTP:%{http_code} time:%{time_total}s size:%{size_download}\n' \
  -F 'file=@某扫描件.pdf;type=application/pdf' http://127.0.0.1:9302/parse
# 基准：HTTP:200 time:~2s；解压有 result.md（UTF-8 无 BOM，内容为文档 Markdown）
python -c "import zipfile; z=zipfile.ZipFile('/tmp/acc.zip'); print(z.namelist(), len(z.read('result.md')))"

# ③ 格式守卫（docx 必拒）
curl -s -m 30 -w 'HTTP:%{http_code}\n' -F 'file=@某文件.docx' http://127.0.0.1:9302/parse
# 基准：HTTP:400 + {"status":"error","message":"研发中心通道(bellm)仅支持 PDF/PNG/JPG/JPEG，收到: ...docx"}

# ④ PNG 图片通道
curl -s -m 120 -o /tmp/acc_png.zip -w 'HTTP:%{http_code} time:%{time_total}s\n' \
  -F 'file=@某页.png;type=image/png' http://127.0.0.1:9302/parse
# 基准：HTTP:200 time:~2s

# ⑤ 版本复验（与 §1.2 逐行一致）
docker exec mineru2doc-bellm python -m pip freeze | sort

# ⑥ 从调用方机器再打一遍 ②③（跨机网络验证）
```

已知行为速查：带插图 PDF → zip 内 `images/<hash>.jpg` + md 相对引用（解压即可渲染）；性能基线 42 页 PDF 28s（≈0.7s/页）、单页扫描 1.3-2s。

## 6. 故障排查

| 症状 | 定位与处置 |
|---|---|
| 容器启动即退、循环重启 | fail-fast：`docker logs` 见 `RuntimeError: ... MINERU_BELLM_AUTH` → `.env` 补凭证后 `docker rm -f` 再 run（docker run 方式改 env 必须重建容器，env 不热加载） |
| §3.3 build 报 wheel 找不到 | wheels 不全或平台不对：回构建机核对 §1.2 清单与 `--platform` 参数；**不许临时联网补包**（破坏离线与钉版承诺） |
| 接口 502 | 上游研发中心网关问题，与本服务无关；有网机器用仓库根 `test_mineru_gateway.py` 直连 `128.23.74.3:9091` 分界 |
| 偶发连接超时 | 网关侧连接层偶发抖动，重试 |
| pip freeze 与 §1.2 有出入 | 构建环节混入了非钉版产物：按 §3 `--no-cache` 重建，勿手补 |
| Windows 构建机自测 python 连不上、curl 通 | 构建机 Clash 注册表代理黑洞内网段；服务端已内置 `trust_env=False`，自写脚本需同样处理 |

## 7. 版本漂移防护原则（给未来的自己）

1. 基础镜像引用**永远带 digest**（§3.2 的 FROM），tag 只做人类可读备注。
2. wheels 目录是**事实快照**：换任何包之前必须重新走 §1.2 复验。
3. 主仓 `Dockerfile` 目前用 `httpx==0.28.*` 通配（在线构建可接受）——**本离线流程一律不用通配**；若主仓 Dockerfile 将来改为钉版，以 `pip freeze` 复验为准同步本文档 §1.2。
4. 升级任何包 = 重走「改清单 → 重新 download wheels / 重新 build → save → load → 六步验证」全流程，不做增量补丁。

---

*版本清单实测来源：2026-10-30 生产容器 `mineru2doc-bellm`（镜像 145MB，2026-10-30 14:18 构建，Python 3.12.12，基础镜像 python:3.12-slim-bookworm @sha256:34386ef0...）。实施与验收过程见 `docs/superpowers/plans/2026-10-30-parserdoc-by-rdmineru.md` §7。*
