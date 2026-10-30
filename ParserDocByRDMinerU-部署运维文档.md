# ParserDocByRDMinerU（RD MinerU 通道 · mineru2doc-bellm）部署运维文档

| 项 | 内容 |
|---|---|
| 服务 | `mineru2doc-bellm` 容器，对外 `http://128.23.67.112:9302` |
| 通道释义 | **RD = 研发中心**。本服务把文档转发给研发中心 MinerU 网关（bellm）解析，返回 Chai 契约 v2.0（与 9300 逐字段兼容） |
| 首次上线 | 2026-10-30，merge `b8f2c93`，验收 6/6 通过 |
| 面向读者 | 在 112 上部署/运维本服务的人 |
| 关联文档 | 《ParserDocByRDMinerU接口对接文档.md》（调用方视角）、《Document2Chunk-HPC112对外接口明细调查.md》§8、《DEPLOY_112.md》（d2c 9301 侧） |

---

## 1. 版本关系：RD 与 HPC 的 MinerU 版本差异已被适配层消化

两套引擎**版本不同且不需要统一**，差异全部收敛在 `mineru2doc/loader.py` 的 loader 接缝：

| | RD 通道（本服务 :9302） | HPC 通道（:9300） |
|---|---|---|
| 上游引擎 | 研发中心 bellm 网关（`128.23.74.3:9091`，MinerU **1.x 系** web_api） | 宿主 MinerU `:9030`（**3.4.2**，protocol_version 2） |
| 请求形态 | `file`（单数）+ Basic 认证 + `parse_method` | `files`（复数）+ `backend=hybrid-engine` |
| 响应形态 | 极简 `{"md_content", "images"?}`（images 为 **data-url 前缀** base64） | `{status, results{<name>:{md_content, content_list}}}` |
| 适配代码 | `BellmLoader`（`MINERU_ENGINE=bellm`） | `HttpLoader`（`MINERU_ENGINE=local`，默认） |
| markdown 后处理 | 共用同一条链（RegexJudge → normalize → render） | 同左 |

**PDF 解析能力实测**（2026-10-30 部署验收，直接打 9302）：21KB 单页扫描 PDF → **HTTP 200 @ 1.9s**，`result.md` 448 字符内容正确；PNG → 200 @ 2.1s；docx → 400 前置守卫。e2e（带插图 PDF）`refs == saved` 两轮真连通过。结论：**RD 版本接口可正常分析 PDF**。

---

## 2. 前置条件

| 项 | 要求 |
|---|---|
| 主机 | 112（128.23.67.112），Docker + docker compose v2 |
| 代码 | `/home/user/document2chunk`（属主 `user`，git 操作用 `sudo -u user -H`，远端只认 **gitea**） |
| 凭证 | 研发中心提供的 Basic 密钥（`Basic <base64>`），写入 `/home/user/document2chunk/.env` 的 `MINERU_BELLM_AUTH=`。**缺失则容器启动即退（fail-fast），进不了健康期** |
| 网络 | 112 → `128.23.74.3:9091` 可达（容器直连，不经 host.docker.internal） |
| 端口 | 9302 空闲（9300/9301 已占用） |

## 3. 构建镜像

镜像 `mineru2doc:latest`，`python:3.12-slim` 基底，只拷贝 `mineru2doc/` 包，依赖仅 `httpx==0.28.* fastapi uvicorn python-multipart`（构建约 2-5 分钟，走 Dockerfile 默认源）。

```bash
ssh root@128.23.67.112
cd /home/user/document2chunk          # 必须在此目录（compose build context = .）
docker compose -f docker-compose.yml build mineru2doc-bellm
```

> ⚠️ **共享镜像 tag 注意**：`mineru2doc:latest` 是 9300 与 9302 **两个容器共用**的 tag。
> 重建会刷新该 tag：在跑的 9300 容器不受影响（继续用旧镜像层），直到它下次被 recreate
> ——届时换上新镜像，行为等价（默认引擎 `local` 未变，9300 的 health 只是多一个
> `"engine":"local"` 字段）。这已在部署验收中确认。

> ⚠️ **buildx bake 卡死**（已知问题，老机器顽疾）：若 `build`/`up --build` 长时间无输出，
> `ps aux | grep -E 'docker compose|buildx bake' | grep -v grep` 找到进程 `kill` 后清场重来，
> 严禁并发第二个构建（compose 项目锁会互卡）。

## 4. 部署与启动

### 4.1 标准流程（112 源码构建，现行方式）

```bash
# ① 代码进 main 并推送（本机）
git checkout main && git merge --no-ff feat/<分支> && git push gitea main && git push origin main

# ② 112 拉取（注意：gitea-only + sudo -u user）
ssh root@128.23.67.112
cd /home/user/document2chunk
sudo -u user -H git pull gitea main
git log --oneline -1                  # 确认到位

# ③ 凭证（幂等追加；只做一次）
grep -q '^MINERU_BELLM_AUTH=' .env || echo "MINERU_BELLM_AUTH=Basic <研发中心提供的base64>" >> .env

# ④ 构建并只启动本服务（--no-deps --no-build 见 §3/§7 权衡）
docker compose -f docker-compose.yml up -d --build mineru2doc-bellm

# ⑤ 健康确认
docker ps --filter name=mineru2doc-bellm --format '{{.Status}}'   # → Up X (healthy)
docker logs mineru2doc-bellm --tail 5
```

**环境变量**（compose 已固定，无需手工传）：

| 变量 | 值 | 说明 |
|---|---|---|
| `MINERU_ENGINE` | `bellm` | 引擎开关（默认 `local` 是 9300 的世界） |
| `MINERU_BELLM_BASE_URL` | `http://128.23.74.3:9091/bellm/MinerU`（默认） | loader 拼接 `/file_parse` |
| `MINERU_BELLM_AUTH` | `.env` 注入 | **必填**；空值 → create_app 抛 RuntimeError → 容器重启循环 |

限额 2 CPU / 2G；healthcheck 30s 间隔探测容器内 `/health`，3 次失败判 unhealthy。

### 4.2 离线镜像包（备选：目标机不能构建时）

```bash
# 构建机（有完整源码处）
docker compose -f docker-compose.yml build mineru2doc-bellm
docker save mineru2doc:latest | gzip > mineru2doc-image.tar.gz
scp mineru2doc-image.tar.gz root@128.23.67.112:/tmp/

# 112
docker load < /tmp/mineru2doc-image.tar.gz
cd /home/user/document2chunk && docker compose -f docker-compose.yml up -d --no-build mineru2doc-bellm
```

> 共享 tag 同样适用于 load：load 后 9300 若被 recreate 也会用新镜像（行为等价）。

## 5. 接口验证（六步，全部有实测基准）

```bash
# ① 探活：必须含 "engine":"bellm"
curl -s -m 10 http://128.23.67.112:9302/health
# 实测：{"status":"ok","version":"0.1.0","mineru_base_url":"http://host.docker.internal:9030","engine":"bellm"}
# （mineru_base_url 是历史字段，bellm 通道实际不使用，恒显 9030 地址——已知行为）

# ② zip 模式 PDF（核心功能）
curl -s -m 300 -o /tmp/acc.zip -w 'HTTP:%{http_code} time:%{time_total}s size:%{size_download}\n' \
  -F 'file=@某扫描件.pdf;type=application/pdf' http://128.23.67.112:9302/parse
# 实测：HTTP:200 time:1.9s size:399；解压得 result.md（UTF-8 无 BOM）
python -c "import zipfile; z=zipfile.ZipFile('/tmp/acc.zip'); print(z.namelist(), len(z.read('result.md')))"

# ③ 格式守卫（docx 必拒）
curl -s -m 30 -o /dev/null -w 'HTTP:%{http_code}\n' -F 'file=@某文件.docx' http://128.23.67.112:9302/parse
# 实测：HTTP:400 + {"status":"error","message":"研发中心通道(bellm)仅支持 PDF/PNG/JPG/JPEG，收到: ...docx"}

# ④ PNG（图片通道）
curl -s -m 120 -o /tmp/acc_png.zip -w 'HTTP:%{http_code} time:%{time_total}s\n' \
  -F 'file=@某页.png;type=image/png' http://128.23.67.112:9302/parse
# 实测：HTTP:200 time:2.1s

# ⑤ 遗留容器回归嗅探（必须 Up 旧时长，不是刚重启）
docker ps --filter name=document2chunk --filter name=mineru2doc --format '{{.Names}} {{.Status}}'
# 实测：两者均 "Up 2 weeks (healthy)"

# ⑥ 9300 健康嗅探
curl -s -m 10 http://128.23.67.112:9300/health     # → {"status":"ok",...} 即可
```

**已知行为速查**（对接方文档有完整表）：

| 场景 | 行为 |
|---|---|
| 非 PDF/PNG/JPG/JPEG | 400 `{"status":"error","message":"研发中心通道(bellm)仅支持 PDF/PNG/JPG/JPEG，收到: <名>"}` |
| zip 模式解析失败（网关 401/非 200/不可达） | 502 `{"status":"error","message":"MinerU 调用失败：…"}` |
| 带插图 PDF | zip 内 `images/<hash>.jpg`，md 相对引用 `images/<hash>.jpg`，解压即可渲染（e2e 定案） |
| 性能基线 | 42 页 PDF 28s（≈0.7s/页）；单页扫描 1.3-2s |

## 6. 故障排查

| 症状 | 定位与处置 |
|---|---|
| 容器反复重启/启动即退 | fail-fast 生效：`docker logs mineru2doc-bellm` 见 `RuntimeError: MINERU_ENGINE=bellm 需要 MINERU_BELLM_AUTH` → 补 `.env` 后重新 `up -d`（需 recreate 才重读 env） |
| `up --build` 卡住无输出 | buildx bake 卡死：杀 `docker compose`/`buildx bake` 进程清场；若镜像已存在改用 `up -d --no-build --force-recreate mineru2doc-bellm` |
| 接口 502 | 上游网关问题，与本服务无关：用仓库根 `test_mineru_gateway.py` 直连 `128.23.74.3:9091` 复测——网关也挂则找研发中心 |
| 本机（Windows）测试 python 客户端连不上、curl 却通 | 本机 Clash 注册表代理黑洞 128.23.x；服务端代码已内置 `trust_env=False`，自写脚本需同样处理 |
| 偶发连接超时（curl/python 均可能） | 该网段连接层偶发抖动，重试即可 |
| 9300 的 `/health` 没有 `engine` 字段 | 9300 还在跑旧镜像（行为等价）；下次 recreate 自动换新 |

## 7. 回滚

```bash
# 代码级回滚（推荐）：回到上一个 main 状态重建本服务
cd /home/user/document2chunk
sudo -u user -H git checkout <回滚目标commit> -- docker-compose.yml mineru2doc/
docker compose -f docker-compose.yml up -d --build --force-recreate mineru2doc-bellm
```

- 回滚**只影响 9302**；9300/9301 与本服务无共享运行时（仅共享镜像 tag，见 §3 注意）。
- 若需保留当前镜像再构建，先 `docker tag mineru2doc:latest mineru2doc:backup-$(date +%m%d)`。

---

*本文档所述行为与实测值来自 2026-10-30 部署验收（merge `b8f2c93`）与 2026-10-27/28 网关实测；实施过程见 `docs/superpowers/plans/2026-10-30-parserdoc-by-rdmineru.md` §7 执行记录。*
