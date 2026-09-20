# Document2Chunk — Excel 轨 A · 112 部署测试报告

> 测试日期：2026-09-20 ~ 09-21。环境：112（128.23.67.112:9301，容器 document2chunk），代码 `fed023f`，镜像新增 `libreoffice-calc`。
> 结论：**轨 A 全功能在 112 部署验收通过**。18/18 样本达成设计预期，其中难点 19（假扩展名 → soffice 归一化）为首次真环境验证通过。
> 测试执行方式：112 宿主机 curl 打 `127.0.0.1:9301/parse-excel`，脚本 `scripts/_excel_112_sweep.sh`，原始记录 `_forensics/excel_112_report_raw.md`。

---

## 1. 部署过程发现并修复的两个真 Bug

实测的价值在这次体现得淋漓尽致——两个 bug 都是本地测试无法暴露的。

### Bug A：镜像 LibreOffice 无 Calc 组件（部署阻断级）

- **现象**：假扩展名样本（真 .xls 改名 .xlsx）经 soffice 归一化必然失败：`"source file could not be loaded"`（rc=0 无输出文件）→ 422。
- **根因**：`Dockerfile.d2c` 只装 `libreoffice-core/writer/impress`——**没有 `libreoffice-calc`**。老格式链路此前只转 .doc（Writer 滤镜），Calc 滤镜从未被需要过；Excel 接入后这是第一次踩到。
- **证据**：容器内 `ls /usr/lib/libreoffice/program/ | grep sclo` 为空（无 `libsclo.so`）；修复后存在且 `--convert-to xls` 成功。
- **修复**：`f94883e` apt 列表补 `libreoffice-calc`。

### Bug B：全空行碎切——一张表被切成 34 片、每片误立表头（正确性级）

- **现象**：P0 天花板文件 `2.1-2022…初步评审` 的主 sheet（102×34，每条项目记录占 3 行、记录间全空行）被切成 35 个 region，34 个"小表"各自把数据行当表头，输出键为 `3.0`、`基于结构性能协调的…` 等垃圾键，仅 41 行。
- **根因**：`boundary.py` 的空行横切正是调研报告 §3.2 被我们 YAGNI 掉的"修正规则②"对应场景——真实语料 P0 文件证明它不是可选优化而是必需品。
- **修复**：`fed023f` 落地 `merge_stacked_regions`——链长 ≥3、恰好隔 1 空行、列跨度重叠（含同带列碎片）→ 回并为单一 region；链长 2 保守不合并（两张独立表仅隔 1 空行结构不可区分）。
- **效果**：主 sheet 41 → **99 行、零垃圾键**（`2022年下半年…-序号`，标题作为表头路径第一级）；评审人员分 sheet 0 → **64 行**评分记录（`评审: 何志军; …-序号: 18; …`）。

### 连带决策偏离（待人工追认）：表单触发器休眠（Q19 偏离）

修复 B 的过程中确认：**任何"横幅合并→表单式"的几何判据都会把带标题/分组表头的数据表整表聚合成 1 个块**（99/230 行丢失，灾难性大于漏判）。真实语料里所有"表单式外观"的 sheet（评审人员分/经费决算表/边坡巡查）行均为有效记录。故 `fed023f` 将表单触发器休眠：classify 只出 `data_table`/`doc_sheet`，`FORM_SHEET` 枚举与 parser 分支保留但不再触发。
**该决策偏离 Q19（表单式→轨 A 整块聚合）**，依据是语料实证；如需恢复，触发器代码可回插。

### 运维教训（已入记忆）

- 本地杀 ssh **不会**杀远端 `docker compose up --build`（buildx bake 子进程存活）——僵尸 compose 与新构建抢项目锁，致 build2 静默僵死（log mtime 停 8 分钟）。重建前须清场 `ps aux | grep -E 'docker compose|buildx bake'`。
- aliyun 源对大文件间歇降速（56MB 包拖 10-25 分钟），全量重建约 80-100 分钟。

---

## 2. 最终扫描结果（18 样本，修复后容器）

### 2.1 real/（人工真实业务样本，kxx-docs P0 全集）

| 文件 | HTTP | rows | blocks | 结果 |
|---|---|---|---|---|
| 2.1-2022…初步评审（调序后）.xlsx | 200 | **163** | 0 | ✅ 碎片表修复生效：主表 99 行干净键 + 评审人员分 64 行评分记录 |
| 项目自定义导出数据 (3) -项目清单2026.3.3.xlsx | 200 | 935 | 0 | ✅ 外部链接 + 51 个无缓存公式双告警；9.9s/938 行 |
| 项目自定义导出数据 (基础)-最新.xlsx | 200 | **230** | 0 | ✅ 三级扁平键 `项目基础信息-基本信息-序号`（Bug B 回归主样本） |
| 7-广东省…R&D经费决算表及支出说明（必选）.xlsx | 200 | 49 | 0 | ✅ 合计行 from_total 打标（本地已验） |
| **20- 软弱地层…-其他.xlsx（假 .xlsx）** | **200** | **81** | 4 doc | ✅ **难点 19 首次真环境通过**：指纹→soffice 归一化→解析；修复前为 422 |

### 2.2 synthetic/（机器生成样本，与人工样本分离存放）

| 文件 | HTTP | 结果 |
|---|---|---|
| **fake_xlsx_ole2.xlsx**（容器 soffice 铸造的真 .xls 改名） | **200** | ✅ 5 行 `科目/金额（元）`，人员费 1000——soffice 链路端到端 |
| csv_utf8.csv / csv_gbk_semicolon.csv | 200 | ✅ 编码嗅探 + 分号分隔符探测 |
| csv_no_header.csv | 200 | ⚠️ 首行当表头（`甲/1` 成键）——已知限制，见 §4 |
| csv_empty.csv | 422 | ✅ 空文件明确报错 |
| broken.xlsx（PK 垃圾） | 400 | ✅ 毒输入拒收 |
| ~$a.xlsx（锁文件形状） | 400 | ✅ 拒收 |
| empty.xlsx | 200 + warning | ✅ 空 sheet 不崩 |
| hidden_sheet.xlsx | 200 + warning | ✅ 隐藏 sheet 跳过、隐藏列数据保留 |
| multi_header.xlsx | 200 | ✅ 三级扁平键 + 分组合并 |
| percent_currency.xlsx | 200 | ✅ `12.6%` / `¥1024.50` / ISO 日期 |
| totals.xlsx | 200 | ✅ keyword + checksum 双合计标记 |

chai 旧端点对 xlsx：HTTP 400，文案指向 `/parse-excel` ✅。

### 2.3 性能抽样

938×40 宽表 9.9s（含 openpyxl 格式二遍）；97KB 小表 37-70ms；假 .xlsx 含 soffice 转换 1.9-2.6s。满足当前语料规模（最大 2.2MB），50 万行级基准仍属 P1 待办。

---

## 3. 验收对照（移交方案 §5.4）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 全量回归 | ✅ 本地 453 passed / 3 skipped（fed023f）；112 容器 18/18 |
| 2 | P0 语料 + 假 .xlsx | ✅ **全部通过**（含此前 skip 的难点 19） |
| 3 | 对照组不误判 | ✅ 通讯录 data_table、规整表逐行全等 |
| 4 | 键形 | ✅ 三级扁平键/撞名（2）/列N 全部在线上复现 |
| 5 | 合计行打标 | ✅ keyword + checksum 双证据 |
| 6 | 警告文案 | ✅ 外链/透视/隐藏/无缓存四类 |

**验收结论：通过。** 附带条件：§1 表单触发器休眠的 Q19 偏离待追认（默认维持现行为）。

---

## 4. 遗留与后续

1. **csv 无表头样本**首行被当表头（`甲/1` 成键）——N4 范围的已知限制，可复用表头类型对比启发式改进（P1）。
2. **标题前缀噪音**：带标题行的表，标题会作为表头路径第一级（`2022年下半年…-序号`）——语义无损但键偏长，P1 可评估"纯通栏单格行降级为 sheet 级标题"的保守规则（注意 DOCX doc_title 同款陷阱，需带对照回归）。
3. P1 backlog 不变：合成用例回归库扩充、50 万行性能基准、单元格内换行值处理、第三方生成器兼容深化。
4. 112 样本库留存：`/home/user/document2chunk-test/excel/{real,synthetic}/`（real=人工 5 件勿动；synthetic=12 件机器生成，可由 `scripts/_gen_excel_synthetic.py` + 容器 soffice 重建），README 标明来源隔离。后续测试直接复用。

## 5. 提交与部署轨迹

| commit | 内容 |
|---|---|
| `066e991` | feat(excel): 轨 A 全量代码+测试（Qoder 执行） |
| `96a11b7` | docs(excel): 文档体系六件 |
| `f94883e` | fix(excel): Dockerfile 补 libreoffice-calc（Bug A） |
| `fed023f` | fix(excel): 碎片表回并 + 表单触发器休眠（Bug B + Q19 偏离） |

均已推 origin(GitHub) + gitea；112 部署于 `fed023f`，容器 healthy。
