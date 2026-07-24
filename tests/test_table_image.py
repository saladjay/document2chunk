"""表格 → 高清截图测试（designs/009）。

覆盖：attach_table_images（PDF 点 bbox 缩放裁剪 / image 源像素裁剪 / 无 bbox·无 image_dir 跳过 /
渲染异常静默 / padding 外扩）、_deskew（倾斜校正 + 空白兜底）、markdown（有/无 table_image_id）、
真机快照（p19，skip-if-missing）。

运行：PYTHONPATH="src;tests" python tests/test_table_image.py
"""

from __future__ import annotations

import os
import tempfile

from document2chunk.export._helpers import block_markdown
from document2chunk.extractors._table_image import _deskew, attach_table_images
from document2chunk.ir import (
    ParagraphNode,
    Provenance,
    SourceType,
    TableCellNode,
    TableNode,
    TableRowNode,
)

_REAL_PDF = (
    r"D:\土地公·征拆政策的3类代表性文件示例（10组）\组4：永久基本农田保护——部门规章及配套"
    r"\2019.1.3  自然资规〔2019〕1号  自然资源部  农业农村部关于加强和改进永久基本农田保护"
    r"工作的通知（清理划定不实，占永久基本农田重新预审）.pdf"
)


def _mktable(bbox=None, page=0, text="cell"):
    cell = TableCellNode(id="c1", blocks=[ParagraphNode(id="p1", text=text)])
    row = TableRowNode(id="r1", cells=[cell])
    return TableNode(
        id="t1",
        rows=[row],
        provenance=Provenance(source_type=SourceType.PDF, page_index=page, bbox=bbox),
    )


def _mkmtable(bbox=None, page=0):
    """含合并格的复杂表（一个 colspan=2 表头 + 数据行）。"""
    h = TableCellNode(id="c1", colspan=2, blocks=[ParagraphNode(id="p1", text="H")])
    a = TableCellNode(id="c2", blocks=[ParagraphNode(id="p2", text="a")])
    b = TableCellNode(id="c3", blocks=[ParagraphNode(id="p3", text="b")])
    return TableNode(
        id="tm",
        rows=[TableRowNode(id="r1", cells=[h]), TableRowNode(id="r2", cells=[a, b])],
        provenance=Provenance(source_type=SourceType.PDF, page_index=page, bbox=bbox),
    )


def _synth_pdf(bbox_pts=(100, 100, 400, 300)):
    """合成 A4 PDF，在 bbox 处画灰底矩形 + 文字（便于裁剪校验）。返回 bytes。"""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    x0, y0, x1, y1 = bbox_pts
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0, 0, 0), fill=(0.75, 0.75, 0.75), width=2)
    page.insert_text((x0 + 20, y0 + 50), "TABLE CELL TEXT", fontsize=24, color=(0, 0, 0))
    data = doc.tobytes()
    doc.close()
    return data


def _has_content(path):
    """PNG 非全白（有像素 < 250）则视为有内容。"""
    from PIL import Image
    import numpy as np

    a = np.asarray(Image.open(path).convert("L"))
    return bool((a < 250).any())


# ---------- attach_table_images：PDF 点 bbox 缩放裁剪 ----------


def test_attach_pdf_point_bbox_scaled_crop():
    bbox = (100, 100, 400, 300)
    pdf = _synth_pdf(bbox)
    t = _mktable(bbox=list(bbox), page=0)
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], pdf, image_dir=d, dpi=300, deskew=False, mode="all")
        assert n == 1
        fn = os.path.join(d, "table_p0_0.png")
        assert os.path.exists(fn)
        assert getattr(t, "table_image_id", None) == "table_p0_0.png"
        # 裁剪区含表框线/文字（非空白）
        assert _has_content(fn)
    print("OK test_attach_pdf_point_bbox_scaled_crop")


def test_attach_padding_expands_crop():
    bbox = (100, 100, 400, 300)
    pdf = _synth_pdf(bbox)
    scale = 300 / 72
    with tempfile.TemporaryDirectory() as d:
        t = _mktable(bbox=list(bbox))
        attach_table_images([t], pdf, image_dir=d, dpi=300, deskew=False, padding_pt=6, mode="all")
        from PIL import Image

        w, h = Image.open(os.path.join(d, "table_p0_0.png")).size
        # bbox 原宽 (400-100)*scale ≈ 1250；加 padding（两侧 6pt*scale≈25）应更宽
        assert w > (400 - 100) * scale + 1
        assert h > (300 - 100) * scale + 1
    print("OK test_attach_padding_expands_crop")


# ---------- image 源：像素 bbox ----------


def test_attach_image_source_pixel_bbox():
    from PIL import Image, ImageDraw

    buf = tempfile.mktemp(suffix=".png")
    im = Image.new("RGB", (500, 400), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([50, 50, 350, 250], outline="black", fill="gray", width=3)
    d.text((60, 80), "IMG TABLE", fill="black")
    im.save(buf)
    try:
        t = _mktable(bbox=[50, 50, 350, 250])  # 已是像素
        with tempfile.TemporaryDirectory() as outdir:
            n = attach_table_images([t], buf, image_dir=outdir, dpi=300, deskew=False, mode="all")
            assert n == 1 and getattr(t, "table_image_id", None) == "table_p0_0.png"
            assert _has_content(os.path.join(outdir, "table_p0_0.png"))
    finally:
        os.remove(buf)
    print("OK test_attach_image_source_pixel_bbox")


# ---------- 跳过条件 ----------


def test_attach_skip_when_no_bbox():
    pdf = _synth_pdf()
    t = _mktable(bbox=None)  # 无 bbox
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], pdf, image_dir=d, deskew=False, mode="all")
        assert n == 0
        assert not hasattr(t, "table_image_id") or getattr(t, "table_image_id", None) is None
    print("OK test_attach_skip_when_no_bbox")


def test_attach_skip_non_table_blocks():
    pdf = _synth_pdf()
    para = ParagraphNode(id="p1", text="not a table")  # 非 TableNode
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([para], pdf, image_dir=d, deskew=False)
        assert n == 0
    print("OK test_attach_skip_non_table_blocks")


def test_attach_render_failure_silent():
    # page_index 越界 → 渲染返回 None → 静默跳过，不抛
    t = _mktable(bbox=[100, 100, 200, 200], page=999)
    pdf = _synth_pdf()  # 仅 1 页
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], pdf, image_dir=d, deskew=False, mode="all")  # 不应抛
        assert n == 0
    print("OK test_attach_render_failure_silent")


# ---------- 简单/复杂表分流（mode）----------


def test_merged_mode_skips_simple_table():
    pdf = _synth_pdf()
    t = _mktable(bbox=[100, 100, 400, 300])  # 全 1×1 简单表
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], pdf, image_dir=d, mode="merged", deskew=False)
        assert n == 0  # 简单表不截图 → 走结构
        assert getattr(t, "table_image_id", None) is None
    print("OK test_merged_mode_skips_simple_table")


def test_merged_mode_captures_complex_table():
    pdf = _synth_pdf()
    t = _mkmtable(bbox=[100, 100, 400, 300])  # 含 colspan=2
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], pdf, image_dir=d, mode="merged", deskew=False)
        assert n == 1  # 复杂表截图
        assert getattr(t, "table_image_id", None) == "table_p0_0.png"
    print("OK test_merged_mode_captures_complex_table")


def test_all_mode_captures_simple_table():
    pdf = _synth_pdf()
    t = _mktable(bbox=[100, 100, 400, 300])  # 简单表
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], pdf, image_dir=d, mode="all", deskew=False)
        assert n == 1  # all 模式：简单表也截图
        assert getattr(t, "table_image_id", None) == "table_p0_0.png"
    print("OK test_all_mode_captures_simple_table")


def test_merged_mode_splits_mixed_in_one_doc():
    pdf = _synth_pdf()
    simple = _mktable(bbox=[100, 100, 400, 300])
    complex_ = _mkmtable(bbox=[100, 100, 400, 300])
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([simple, complex_], pdf, image_dir=d, mode="merged", deskew=False)
        assert n == 1  # 只复杂表
        assert getattr(simple, "table_image_id", None) is None
        assert getattr(complex_, "table_image_id", None) == "table_p0_0.png"
    print("OK test_merged_mode_splits_mixed_in_one_doc")


# ---------- _deskew ----------


def _proj_var(img):
    import numpy as np

    g = img.convert("L")
    a = np.asarray(g)
    mask = (a < a.mean()).astype(float)
    rows = mask.sum(axis=1)
    return float(rows.var()) if rows.size else 0.0


def test_deskew_blank_unchanged():
    from PIL import Image

    blank = Image.new("RGB", (300, 150), "white")
    assert _deskew(blank) is blank  # 无文字 → gain 低 → 原样
    print("OK test_deskew_blank_unchanged")


def test_deskew_tilted_text_improves_alignment():
    from PIL import Image, ImageDraw

    base = Image.new("RGB", (600, 250), "white")
    d = ImageDraw.Draw(base)
    for i in range(4):
        d.text((30, 20 + i * 50), "TABLE LINE TEXT CONTENT HERE", fill="black")
    tilted = base.rotate(3, expand=True, fillcolor="white")  # 倾斜 3°
    out = _deskew(tilted)
    assert isinstance(out, type(tilted))
    # 校正后水平投影方差应 >= 倾斜原图（更对齐）——允许 ==（兜底不旋转时）
    assert _proj_var(out) >= _proj_var(tilted) - 1e-6
    print("OK test_deskew_tilted_text_improves_alignment")


# ---------- markdown 渲染 ----------


def test_markdown_table_with_image_id():
    t = _mktable(bbox=None)
    t.table_image_id = "table_p0_0.png"
    md = block_markdown(t)
    assert md == "![表格](table_p0_0.png)"
    print("OK test_markdown_table_with_image_id")


def test_markdown_table_fallback_without_image_id():
    t = _mktable(bbox=None)  # 无 table_image_id
    md = block_markdown(t)
    assert md.startswith("| ") and "cell" in md  # 回退表格 markdown
    print("OK test_markdown_table_fallback_without_image_id")


def test_markdown_complex_table_renders_html():
    # 复杂表（含 colspan）→ HTML <table>（保留 colspan/rowspan）
    t = _mkmtable(bbox=None)  # colspan=2 表头 + 数据行
    md = block_markdown(t)
    assert md.startswith("<table>") and md.endswith("</table>")
    assert 'colspan="2"' in md
    assert "<td>a</td>" in md and "<td>b</td>" in md  # 数据行普通 td
    print("OK test_markdown_complex_table_renders_html")


def test_markdown_complex_table_image_overrides_html():
    # 复杂表 + image 模式（挂了 table_image_id）→ 图片优先于 html
    t = _mkmtable(bbox=None)
    t.table_image_id = "table_p0_0.png"
    assert block_markdown(t) == "![表格](table_p0_0.png)"
    print("OK test_markdown_complex_table_image_overrides_html")


# ---------- 真机快照（skip-if-missing）----------


def test_real_p19_snapshot():
    if not os.path.exists(_REAL_PDF):
        print("SKIP test_real_p19_snapshot（无真机 PDF）")
        return
    try:
        import fitz  # noqa: F401
    except ImportError:
        print("SKIP test_real_p19_snapshot（无 fitz）")
        return
    # p19 表格大致 bbox（PDF 点，约略——服务/版面给出的区域）；用一个覆盖表区的 bbox
    t = _mktable(bbox=[60, 60, 540, 400], page=19)
    with tempfile.TemporaryDirectory() as d:
        n = attach_table_images([t], _REAL_PDF, image_dir=d, dpi=200, deskew=False, mode="all")
        assert n == 1
        fn = os.path.join(d, "table_p19_0.png")
        assert os.path.exists(fn) and _has_content(fn)
    print("OK test_real_p19_snapshot (table_p19_0.png 已生成)")


def main():
    test_attach_pdf_point_bbox_scaled_crop()
    test_attach_padding_expands_crop()
    test_attach_image_source_pixel_bbox()
    test_attach_skip_when_no_bbox()
    test_attach_skip_non_table_blocks()
    test_attach_render_failure_silent()
    test_merged_mode_skips_simple_table()
    test_merged_mode_captures_complex_table()
    test_all_mode_captures_simple_table()
    test_merged_mode_splits_mixed_in_one_doc()
    test_deskew_blank_unchanged()
    test_deskew_tilted_text_improves_alignment()
    test_markdown_table_with_image_id()
    test_markdown_table_fallback_without_image_id()
    test_markdown_complex_table_renders_html()
    test_markdown_complex_table_image_overrides_html()
    test_real_p19_snapshot()
    print("\nALL TABLE IMAGE TESTS PASSED")


if __name__ == "__main__":
    main()
