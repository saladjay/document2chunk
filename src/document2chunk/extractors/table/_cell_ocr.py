"""每格 OCR 文字：``cell_box_list``（图空间）+ 页图 → ``{box_index: text}``。

**box-bearing 文字**——根治合并表头文字错位（r0 等）。服务的 html 文字无 box、且其 span
已与拓扑脱钩，无法可靠钉到格；本模块对**本地图**OCR，按 OCR poly 中心落点匹配到包含它的
cell box，故文字按构造属于该格。

前置：``cell_box_list`` 必须在**与页图相同的像素空间**（由 ``_render.render_pdf_page`` 出图、
再把该图送服务取得——见 extractor 的 ``geo_ocr`` 模式）。

lazy import paddleocr；``ocr`` 引擎可注入便于单测。
"""

from __future__ import annotations

from typing import Any, Optional, Union

ImageLike = Union[str, Any]  # 文件路径 / PIL.Image / ndarray


def _default_ocr():
    """lazy 创建 paddleocr 引擎（PP-OCRv6，中文，自动文本行方向）。"""
    from paddleocr import PaddleOCR

    return PaddleOCR(lang="ch", use_textline_orientation=True)


def _to_predictable(image: ImageLike):
    """paddleocr ``predict`` 只接受 ``str``(路径) 或 ``ndarray``；PIL.Image → ndarray。"""
    if isinstance(image, str):
        return image
    if hasattr(image, "size") and hasattr(image, "mode"):  # PIL.Image
        import numpy as np

        return np.asarray(image)
    return image  # ndarray 或其它


def _run_ocr(ocr, image: ImageLike):
    """统一调 ``ocr.predict(image)`` → ``[(poly, text), ...]``。"""
    res = ocr.predict(_to_predictable(image))
    if isinstance(res, list):
        res = res[0] if res else {}
    polys = res.get("rec_polys") or res.get("dt_polys") or []
    texts = res.get("rec_texts") or []
    return list(zip(polys, texts))


def _bbox_center(poly):
    """poly（list[(x,y)] 或 ndarray[N,2]）→ ``(cx, cy, y0, x0)``。纯 Python，免 numpy。"""
    xs = [float(pt[0]) for pt in poly]
    ys = [float(pt[1]) for pt in poly]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(ys), min(xs))


def ocr_cell_texts(
    cell_boxes: list[list[float]],
    image: ImageLike,
    *,
    ocr: Optional[Any] = None,
) -> dict[int, str]:
    """``cell_boxes``（图空间 ``[x1,y1,x2,y2]``）+ 页图 → ``{box_index: text}``。

    对每个 cell box，收集**中心落在其内**的 OCR poly 文字，按 ``(y0, x0)``（左上角，阅读序）
    排序拼接。无命中的 box 不出现在结果里（→ 空文字）。

    Args:
        cell_boxes: 与 ``image`` 同像素空间的单元格框。
        image: 页图（路径 / PIL / ndarray）——传给 ``ocr.predict``。
        ocr: paddleocr 引擎（缺省 lazy 创建）。可注入 stub（``predict`` 返回
            ``[{"rec_polys": [...], "rec_texts": [...]}]``）做单测。
    """
    if ocr is None:
        ocr = _default_ocr()
    centered = [(_bbox_center(p), t) for p, t in _run_ocr(ocr, image)]

    out: dict[int, str] = {}
    for bi, b in enumerate(cell_boxes):
        x1, y1, x2, y2 = b
        hits = []
        for (cx, cy, y0, x0), t in centered:
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                hits.append((y0, x0, t))
        if not hits:
            continue
        hits.sort()
        out[bi] = "".join(t for _, _, t in hits)
    return out
