# issues7 — 大文件提效 open issues(等外部决策 + 可自主开工)

**日期**: 2026-09-17
**来源**: `docs/大文件提效调研.md` §5(修复优先级)/§7(效果等价性审查)/§7.1-7.2(round-1/2 实施结果)
**背景**: round-1(api 线程池化/缓存/惰性/去重扫)与 round-2(OCR 页级并发/partial 选项/detect 看门狗)已上线 main@de80319、部署 112;前后对照 PASS 23/FAIL 0。本文件记录**剩余**挂账,不重复已关闭项。
**状态图例**: ⏸ 等外部决策 | ▢ 可自主开工(方案已定,未排期) | 长尾(有价值不紧急)

---

## A. 等外部决策 → ✅ 均已决议(2026-09-17)

### O-1 `/parse-json` 剔除 `ImageNode.data` —— ✅ 已确认并实施

| | |
| --- | --- |
| 决议 | **确认除 Chai 外无消费方**(2026-09-17);Chai 走 /parse-pdf zip 不含此字段 → 剔除安全,已实施 |
| 实施 | `api.py` `_strip_bytes_fields`:序列化树递归剔除 bytes 字段(保守按"值是 bytes 就删",未来新增 bytes 字段自动覆盖);`/parse-json` 改 `model_dump`(比 dumps→loads 少一份大字符串)→ 剔除 → 单次 `json.dumps`。库内 IR 不动(`ImageNode.data` 仍可用),只动 HTTP 出口 |
| 测试 | `tests/test_parse_json_no_image_bytes.py`:端点级(响应无 base64/无 data 字段,image_id/format/markdown/其余节点全保留)+ 单元级 |
| 验收遗留 | 部署后观察 /parse-json 真实响应(当前无生产消费方,风险≈0) |

### O-2 OCR partial(部分结果)默认开 —— ✅ 决议:保持默认关

| | |
| --- | --- |
| 决议 | **保持默认关(全有全无)**(2026-09-17)。依据:Chai 侧机制确认——**只要失败就会重新调用**,失败即重试是唯一自愈通道;partial 的"200+缺页"不会触发重试,缺页将永久静默丢失,故不可默认开 |
| 现状 | partial 保留为**应急选项**:`DOCUMENT2CHUNK_OCR_PARTIAL=1` 手动开启(OCR 服务长时间不可用等场景,知情消费 failed_pages)。默认路径"失败→Chai 重投"即正确设计,无需改动 |
| 后续 | 无。若未来 Chai 改造为能感知 failed_pages 再重议 |

---

## B. ▢ 可自主开工 → ✅ 全部处置完毕(2026-09-17,分支 feat/open-issues-sweep)

### S-1 `/parse-pdf` zip 落盘 + 上传流式 —— ✅ 已实施(2b8c213)

`serve.parse_to_zip_file(data|path, out_path=...)`:zip 直写文件不整包驻留内存;`parse_to_zip` 保留为内存版兼容包装。api 层:上传 1MB 分块流式落盘→传路径→`FileResponse` 流式回传+BackgroundTask 清理——**请求/响应两端内存有界,P0-3 收口**。顺带:zip 路径补齐 S-5a 门控、`_sniff_source_type` 支持路径读文件头(路径模式与字节模式行为对齐)。测试 `test_s1_zip_file`(3)。

### S-2 提取阶段子进程化 —— ✅ 已实施(83a29ce)

`pipeline/pdf_extract_worker`:子进程跑完整 PDF 提取→ExtractionResult JSON 落盘→父进程回载;`extract_pdf_guarded` 超时 SIGKILL→InvalidSourceError。serve PDF 分支默认启用(env `DOCUMENT2CHUNK_EXTRACT_TIMEOUT` 默认 600s,0=关)。**毒 PDF 在 detect 与提取两阶段都被关在子进程里,服务不再可被楔死**。等价:序列化往返逐字节保真(测试断言);代价:每 PDF 一次子进程冷启动 ~0.3-1s。测试 `test_s2_extract_guard`(6)。

### S-3 O(n²) 热点 —— ✅ 改 2 处 + 审查后不改 3 处(8264fbf + 3ac7571)

| 位置 | 处置 |
| --- | --- |
| `merge.py` 段落拼接 O(L²) | **已改**:parts 惰性累积+段尾 join;重复抽取检测 bbox 短路先行,join 仅罕见路径 |
| `is_standalone_line` 页内 O(n²) | **已改**:页级 (center_y,pos) 预排序+二分窗口,同一谓词;老调用路径保留 |
| `pdf.py:359` 行×表 bbox | **不改**:复查确认 table_bboxes 本就同页,表数每页个位数,无优化价值 |
| `image_detection` O(图×元素)/O(图²) | **不改**:页级规模小(图个位数×元素数百),改写不划算 |
| `_reorder_overlapping` 冒泡 | **不改**:冒泡回退带视觉顺序语义,改写等价风险>收益 |

⚠️ **实测教训**:merge 初版无条件取 `current["markdown"]`/重置行取新元素键——30MB 语料文件即 KeyError(harness 当场捕获);已改三处防御式 `.get` 并补两条回归测试(缺键初始化/重置路径)。教训:**手搓 fixture 元素要覆盖"缺可选键"变体**。

### S-4 冗余内存副本 —— ⏸ 调查后决定不做(收益<风险)

- **S-4a** pipeline element 三份文本:`markdown` 键有真实消费方(`toc_detection.py:168-171` 并行拼接),去重需跨 stage 重构,收益(临时 dict 内存)与风险不成比例。**不改**。
- **S-4b** IR `text`+`runs` 双份:属于 /parse-json JSON 契约面,动它=O-1 级契约决策;且两份服务不同消费路径(text 供后处理/导出,runs 供样式导出)。**不做**,如未来要动需单独确认消费方。

### S-5 P2 长尾 —— ✅ 4 项全做(b8ea1c6)

| 项 | 处置 |
| --- | --- |
| legacy 兜底重跑 | ✅ identify 门控:内容可解析(DOCX/PDF/IMAGE)直接重抛首轮错误,省一次整档二次解析;真老格式转换路径不变(守卫测试) |
| geo_ocr 引擎复用 | ✅ PaddleOCR 引擎进程级单例+双检锁 |
| 留痕轮转 | ✅ `_cleanup_save_artifacts` 按天轮转(`DOCUMENT2CHUNK_SAVE_RETENTION_DAYS` 默认 30,0=关) |
| model-runtime 探测 | ✅ active_model 进程级 TTL 缓存(`DOCUMENT2CHUNK_OCR_MODEL_TTL` 默认 300,0=关),失败不入缓存 |

---

## 备注

- **台账状态(2026-09-17 收口)**:A 类 2 项决议关闭(O-1 已实施/O-2 保持默认关),B 类 5 组全部处置完毕(S-1/2/5 已实施,S-3 改 2 处+3 处审查不改,S-4 调查后不做)。**open issues 清零**,剩余仅"部署待办"(分支 feat/open-issues-sweep 未合 main 未部署)。
- round-3 对照:PASS 23 / FAIL 0 / SKIP 6,1.22×(基线 100.2s → 82.4s);全量 386 passed。
- 以上全部属于**大文件提效**工作流。DOCX 解析质量的 P0(A1 开头守卫、doc_title 位置约束,issues6 根因 #1/#2)**不在本台账**,归 docx 质量工作流,勿混淆。
- round-1/2 已关闭项见 `docs/大文件提效调研.md` §7.1/§7.2,不在本文件重复。
- 施工流程:TDD(先红后绿)→ 全量 pytest → equiv harness 对照(基线 `D:\document2chunk-equiv\manifest.baseline.snapshot.jsonl`)→ 合 main 双推 → 112 重建验证。
