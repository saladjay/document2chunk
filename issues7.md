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

## B. ▢ 可自主开工(方案已定,未排期施工)

### S-1 `/parse-pdf` zip 落盘 + FileResponse;上传流式(**建议下一个做**)

| | |
| --- | --- |
| 现状 | 响应 zip 仍在内存 `BytesIO` 拼完整字节再 `Response(content=zip_bytes)`(`serve.py` `parse_to_zip`、`api.py` `_chai_parse`);上传仍 `await upload.read()` 整读。round-1 只修了"事件循环阻塞"这一半,**内存峰值线(P0-3)未动**:143MB 级文档单请求峰值可达 GB,4G 容器 1-2 个并发即 OOM 风险 |
| 方案 | ① `parse_to_zip` 改写临时文件(复用现有 `d2c_zip_` 临时目录模式),`FileResponse(path)` + BackgroundTask 发送后清理;② `/parse-json` 同法;③ 上传侧:`await request.stream()` 落盘(需动 multipart 读取方式,工程量中等,可与 ① 分期) |
| 等价性 | zip 字节内容不变(落盘≠改内容);对照门槛:基线 manifest 全 PASS |
| 验收 | equiv harness 全 PASS + 大文档并发内存实测(容器 stats 观察) |

### S-2 提取阶段子进程化(毒 PDF 完整防线)

| | |
| --- | --- |
| 现状 | round-2 看门狗只护住 detect(已知楔死点,killer.pdf 在 detect 期 `page.get_text` 楔死);**提取期**(`PdfExtractor.extract` 内的 span 提取/渲染/管线)理论上仍可被病态件楔死 worker 线程(线程池工人被占死,并发额度泄漏) |
| 方案 | `PdfExtractor.extract` 整体子进程化:临时文件传入,子进程跑完整提取,产物(ExtractionResult)序列化回传;超时 SIGKILL → InvalidSourceError。可复用 `detect_pdf_type_guarded` 的骨架;需解决 ExtractionResult 序列化往返(pydantic 模型,可行)。备选轻量版:仅给 `page.get_text`/渲染等 MuPDF C 调用密集段包子进程 |
| 等价性 | 子进程同版本同结果;对照门槛同上。注意与 round-1 `skip_detect`、图片落盘目录(image_dir 在子进程侧写,主进程消费)的交互 |
| 验收 | harness 全 PASS + killer.pdf(`_forensics/killer.pdf`)实测:请求 422 快速失败、服务存活、后续请求正常 |

### S-3 P1-3 其余 O(n²) 热点(长尾)

| 位置 | 模式 | 备注 |
| --- | --- | --- |
| `pipeline/stages/classification.py:63-77` → `heading_scorer.py:64-77` | `is_standalone_line` 每元素扫同页全部元素,页内 O(n²) | 页索引化(同 filter_noise 手法)即可等价消除 |
| `pipeline/stages/image_detection.py:194-199,258-261` | O(图×元素) + 页内图片两两求交 O(图²) | 同上 |
| `pipeline/stages/merge.py:73-74` | 段落合并 `text = text + elem` 单段 O(L²) | 改 list-append + join |
| `pdf.py:445-463` | `_reorder_overlapping` 冒泡带回退最坏 O(n²) | 需先证等价(回退语义) |
| `pdf.py:359` | 每行 × 全部表格 bbox 重叠检测 | 表 bbox 按页分组 |

### S-4 P1-4 冗余内存副本(长尾)

| 位置 | 内容 |
| --- | --- |
| `pdf.py:387-409` | pipeline element 同文本存 3 份(`text`/`markdown`/`spans[]`),映射 BlockNode 后才释放——可去 `markdown` 键或延迟构建 |
| `ir/models.py:106-109` + `ocr/_mapping.py:319` + `_geo_reconstruct.py:328-331` | IR 每块 `text`+`runs` 双份存同一文本——去一方需动导出/下游,影响面大,放最后 |

### S-5 P2 长尾

| 项 | 内容 |
| --- | --- |
| legacy 兜底重跑 | 解析失败→识别→soffice→**整条管线重跑**(最坏整档解析 2 次,`serve.py:346-352,427-433`);可把首轮异常分类后只对"疑似老格式"走转换 |
| 表格 geo_ocr 引擎复用 | `_cell_ocr.py:20-24,72-74`:同页 T 张表 = T 次新建 PaddleOCR 引擎 + T 次整页 OCR;引擎提为模块级单例 + 同页 OCR 结果复用 |
| `_save_zip_artifacts` 无轮转 | `serve.py:51-77`:留痕(request.bin/response.zip/解包)只写不清,磁盘慢性泄漏;按大小/天数轮转 |
| OCR model-runtime 探测 | `ocr/extractor.py:70-75`:每次解析都 GET 一次 active_model;可缓存 + TTL |

---

## 备注

- 以上全部属于**大文件提效**工作流。DOCX 解析质量的 P0(A1 开头守卫、doc_title 位置约束,issues6 根因 #1/#2)**不在本台账**,归 docx 质量工作流,勿混淆。
- round-1/2 已关闭项(缓存/惰性/线程池/并发/看门狗等)见 `docs/大文件提效调研.md` §7.1/§7.2,不在本文件重复。
- B 类施工一律走既定流程:TDD(先红后绿)→ 全量 pytest → equiv harness 对照(基线 `D:\document2chunk-equiv\manifest.baseline.snapshot.jsonl`)→ 合 main 双推 → 112 重建验证。
