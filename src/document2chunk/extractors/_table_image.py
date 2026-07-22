"""表格 → 高清截图（designs/009）。

结构识别（designs/008）在复杂合并表头上太弱、易错；改用图片方案——表格区域高清截图
+ 旋转矫正，markdown 里以图片嵌入表格原位置。**保留 TableNode 结构**（JSON/检索可用），
仅额外挂 ``table_image_id``（``_BlockBase`` 的 ``extra="allow"``），markdown 优先渲染图片、
失败回退表格（非破坏、可逆）。

前置：``TableNode.provenance.page_index``（0-based）+ ``provenance.bbox=[x0,y0,x1,y1]``：
PDF/OCR-PDF 为 PDF 点（裁剪 ``×dpi/72``），OCR-image 为源图像素（``×1.0``）。
PDF 仅抽取期可用 → 本函数在 extractor 内调用（与 ``pdf._extract_page_images`` 对称）。

旋转矫正：fitz ``get_pixmap`` 默认应用页面 ``/Rotate``（正向）+ 轻量投影 deskew（扫描倾斜）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

SourceLike = Union[str, Path, bytes]


def _is_pdf_source(source: SourceLike) -> bool:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)[:5] == b"%PDF-"
    return str(source).lower().endswith(".pdf")


def _load_page_image(source: SourceLike, page_index: int, dpi: float):
    """渲染 ``page_index`` 页 → ``PIL.Image``（RGB）。

    PDF：fitz ``get_pixmap``（应用 ``/Rotate``，正向）@ ``dpi``。
    image 源：仅 page 0 有效（单图）。
    """
    from PIL import Image

    if _is_pdf_source(source):
        import fitz  # lazy

        if isinstance(source, (bytes, bytearray)):
            doc = fitz.open(stream=bytes(source), filetype="pdf")
        else:
            doc = fitz.open(str(source))
        try:
            if page_index < 0 or page_index >= doc.page_count:
                return None
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
            mode = "RGB" if pix.alpha == 0 else "RGBA"
            img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
        return img.convert("RGB") if img.mode != "RGB" else img

    # image 源
    if page_index != 0:
        return None
    if isinstance(source, (bytes, bytearray)):
        from io import BytesIO

        return Image.open(BytesIO(bytes(source))).convert("RGB")
    return Image.open(str(source)).convert("RGB")


def _deskew(img, max_angle: float = 5.0, step: float = 0.5, min_gain: float = 1.10):
    """轻量投影 deskew：找使水平投影方差最大的角度；提升不足（``min_gain``）则不旋转。

    纯 numpy + PIL，免 opencv。无 numpy 或异常 → 原样返回（永不阻断）。
    """
    try:
        import numpy as np
    except ImportError:
        return img
    try:
        g = img.convert("L")
        w, h = g.size
        if w > 300:  # 下采样提速
            sc = 300 / w
            g = g.resize((300, max(1, int(h * sc))))
        a = np.asarray(g)
        thr = a.mean()  # 简易阈值（text=暗）
        mask_img = g.point(lambda v: 255 if v < thr else 0)  # text=255

        def proj_var(deg: float) -> float:
            if abs(deg) < 1e-3:
                m = np.asarray(mask_img) > 127
            else:
                rot = mask_img.rotate(deg, expand=False, fillcolor=0)
                m = np.asarray(rot) > 127
            rows = m.sum(axis=1).astype(float)
            if rows.size == 0 or rows.mean() == 0:
                return 0.0
            return float(rows.var())

        base = proj_var(0.0)
        best_ang, best_var = 0.0, base
        ang = -max_angle
        while ang <= max_angle + 1e-9:
            v = proj_var(ang)
            if v > best_var:
                best_var, best_ang = v, ang
            ang += step
        # 提升不足 / 最佳角≈0 → 不旋转
        if best_var < base * min_gain or abs(best_ang) < 1e-3:
            return img
        return img.rotate(best_ang, expand=True, fillcolor=(255, 255, 255))
    except Exception:
        return img


def attach_table_images(
    blocks: list,
    source: SourceLike,
    *,
    image_dir: Union[str, Path],
    dpi: float = 300.0,
    deskew: bool = True,
    padding_pt: float = 6.0,
) -> int:
    """遍历 ``blocks``，给每个有 ``page_index + bbox`` 的 :class:`TableNode` 截图、落盘、
    挂 ``table_image_id``。返回处理的表数。

    - 按 ``page_index`` 分组，每页渲染一次。
    - bbox 缩放到渲染像素（PDF ``×dpi/72``；image ``×1.0``）+ ``padding_pt`` 外扩（含表框线）。
    - 任何失败（渲染/裁剪/落盘异常、无 bbox、越界）静默跳过（TableNode 原样保留）。
    """
    from document2chunk.ir import TableNode

    targets: list[tuple[object, int, list[float]]] = []
    for b in blocks:
        if not isinstance(b, TableNode):
            continue
        prov = getattr(b, "provenance", None)
        if prov is None or prov.page_index is None or not prov.bbox:
            continue
        targets.append((b, prov.page_index, list(prov.bbox)))
    if not targets:
        return 0

    os.makedirs(str(image_dir), exist_ok=True)
    by_page: dict[int, list[tuple[object, list[float]]]] = {}
    for blk, pg, bbox in targets:
        by_page.setdefault(pg, []).append((blk, bbox))

    scale = (dpi / 72.0) if _is_pdf_source(source) else 1.0
    pad = padding_pt * scale
    page_counter: dict[int, int] = {}
    n_done = 0

    for pg in sorted(by_page):
        try:
            img = _load_page_image(source, pg, dpi)
        except Exception:
            img = None
        if img is None:
            continue
        w, h = img.size
        for blk, bbox in by_page[pg]:
            x0, y0, x1, y1 = bbox
            cx0 = max(0, int(x0 * scale - pad))
            cy0 = max(0, int(y0 * scale - pad))
            cx1 = min(w, int(x1 * scale + pad))
            cy1 = min(h, int(y1 * scale + pad))
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            try:
                crop = img.crop((cx0, cy0, cx1, cy1))
                if deskew:
                    crop = _deskew(crop)
                idx = page_counter.get(pg, 0)
                page_counter[pg] = idx + 1
                fn = f"table_p{pg}_{idx}.png"
                crop.save(os.path.join(str(image_dir), fn))
                blk.table_image_id = fn  # _BlockBase extra="allow"
                n_done += 1
            except Exception:
                continue
    return n_done
