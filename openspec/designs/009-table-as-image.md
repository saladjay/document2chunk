# 设计 009 — 表格 → 高清截图嵌入 markdown（image-based tables）

## 1. 上下文与动机

表格**结构识别**（designs/008：html / geo / geo_ocr）在超复杂合并表头上太弱、易错——
colspan/rowspan 错乱、文字落位偏、OCR 噪声，且服务 ``cell_box_list`` 坐标空间未校准（需
图片回调校准）。用户决定改用**图片方案**：表格区域**高清截图 + 旋转矫正**，markdown 里以
图片形式嵌入表格原位置。**完整视觉保真**，绕开识别难题。

**决策**（已确认）：
- **保留 ``TableNode`` 结构**（JSON/检索仍可用），markdown 优先渲染图片、失败回退表格
  （非破坏、可逆）。
- **旋转矫正** = fitz ``/Rotate``（正向）+ 轻量投影 deskew（扫描倾斜），不引重依赖。

## 2. 关键事实

- ``TableNode.provenance.page_index``（0-based int）+ ``provenance.bbox=[x0,y0,x1,y1]``：
  PDF/OCR-PDF 为 **PDF 点**，OCR-image 为**源图像素**。裁剪缩放 PDF ``×dpi/72``、image ``×1.0``。
  来源：``extractors/_mapping.py``（PDF）、``extractors/ocr/_mapping.py``（OCR）。
- **PDF 仅抽取期可用** → 截图在 extractor 内做（与 ``pdf._extract_page_images`` 对称）。
- ``TableNode`` 继承 ``_BlockBase``（``extra="allow"``）→ 挂 ``table_image_id`` **无需改 IR 模型**，
  序列化/反序列化自动保留。
- markdown 已对 ``ImageNode`` 输出 ``![alt](image_id)``；只改 ``TableNode`` 分支即可
  （``export/_helpers.py``）。
- fitz ``get_pixmap`` 默认应用 ``/Rotate``（正向）；**无现成 deskew**（本设计新增）。

## 3. 方案

### 3.1 新模块 ``extractors/_table_image.py``（pdf + ocr 共享）

- ``attach_table_images(blocks, source, *, image_dir, dpi=300, deskew=True, padding_pt=6) -> int``
  - 筛 ``TableNode`` 且有 ``page_index + bbox``；按 ``page_index`` 分组，**每页渲染一次**。
  - PDF → fitz ``get_pixmap``（``/Rotate`` 应用、@ ``dpi``）；image 源 → ``PIL.open``（仅 page 0）。
  - bbox 缩放到渲染像素 + ``padding_pt`` 外扩（含表框线）→ ``crop`` → 可选 ``_deskew`` →
    落盘 ``image_dir/table_p{page}_{idx}.png`` → ``block.table_image_id = fn``（extra 属性）。
  - 任何失败（渲染/裁剪/落盘异常、无 bbox、越界）静默跳过（``TableNode`` 原样保留 → markdown 回退）。
- ``_deskew(img, max_angle=5, step=0.5, min_gain=1.10)``：灰度+阈值→text mask（下采样~300px）；
  对各角度旋转 mask 求水平投影方差，取最大方差角；``gain = V(θ*)/V(0) < min_gain`` 或 ``θ*≈0`` → 不旋转；
  否则原图 ``rotate(θ*, expand=True, 白底)``。纯 numpy+PIL，无 numpy/异常 → 原样返回。

### 3.2 markdown 渲染（``export/_helpers.py``）

``block_markdown`` 的 ``TableNode`` 分支：有 ``table_image_id`` → ``![表格](table_image_id)``；
无 → 回退 ``table_markdown``。

### 3.3 extractor 接入

- ``PdfExtractor.extract``（``pdf.py:603``）：``split_attachments`` 后，有 ``image_dir`` 且
  ``table_image`` → ``attach_table_images`` 对 ``main_content`` + 各 ``attach_segment``。``_attach_table_images_all``。
- ``OcrExtractor.extract``（``ocr/extractor.py:59``）：同（``image_out_dir`` + ``data``）。``_attach_table_images_ocr``。
- options：``ParseOptions`` 加 ``table_image=True`` / ``table_image_dpi=300`` / ``deskew=True``；
  ``pdf._normalize_options`` 白名单同步。
- **默认 True 安全**：无 ``image_dir`` 时不落盘 → 不挂 ``table_image_id`` → markdown 自动回退表格（现行行为不变）。

### 3.4 命名

复用 ``image_dir``/``image_out_dir``；表图前缀 ``table_``，与普通图 ``p{page}_{idx}.{fmt}`` 不冲突。

## 4. 实测

- 单测 ``tests/test_table_image.py``（11 例）：PDF 点 bbox 缩放裁剪、image 源像素裁剪、
  padding 外扩、无 bbox/非表块/越界静默跳过、``_deskew``（空白不动 + 倾斜校正提升方差）、
  markdown（有/无 ``table_image_id``）、p19 真机快照（fitz 渲染落盘）。
- 真机端到端（OCR 路径，自然资规2019-1号 p19）：OCR 检出表 ``bbox=[39,174,782,462]``（PDF 点）→
  挂 ``table_image_id=table_p0_0.png`` → 落盘 **3158×1277** 高清图、有内容 ✓。
- 注：自然资规2019-1号 是**扫描件**，走 OCR 路径；PdfExtractor（span）对扫描件无表（属预期，路由 OCR）。

## 5. 与 designs/008 的关系

- 008 的结构识别能力（``TableExtractor`` / geo / geo_ocr）**保留**——结构化输出（JSON/检索/单元格级
  数据）仍有价值，且图片方案不产出结构。
- 009 是**主流水线 markdown 输出**的默认：表格以图片呈现（视觉保真）。需要结构数据时用 008。
- ``enhance_tables``（008 step④，未实现）若后续做，可让 pdf/ocr 弱表升级为 008 强表结构；与本设计正交。

## 6. 已知限制 / 后续

- **OCR-image bbox 紧度**：版面检测区域可能偏松/紧 → 截图边距。``padding`` 缓解；严重时联合 ``cell_box_list`` 收紧。
- **``api.parse()`` 不透传 ``image_dir``**：现普通图也不透传（统一后续做）；目前直接调 extractor + ``image_dir`` 生效。
- **deskew 仅投影法**：扫描件严重倾斜可后续上 opencv（``minAreaRect``）。
- **表格进附件**：``split_attachments`` 可能把单表页拆到 attachment——本设计已对 main+attach 都截图。

## 7. 模块

``extractors/_table_image.py``（``attach_table_images`` / ``_deskew`` / ``_load_page_image``）；
``export/_helpers.py``（markdown）；``extractors/pdf.py`` + ``extractors/ocr/extractor.py``（接入）；
``api.py``（``ParseOptions``）；``tests/test_table_image.py``（11 例 + 真机快照）。
