"""debug/viz 冒烟测试（pytest-optional：可 ``python tests/test_debug_viz.py`` 直跑）。

覆盖：配色/字体/坐标换算、BlockNode→element 归一化、叠加视图、结构树视图、
源感知降级、debug_dir 过程模式（与旧库等价）。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from PIL import Image

from document2chunk.debug import (
    TYPE_COLORS,
    block_to_element,
    draw_annotations,
    has_pymupdf,
    heading_color,
    load_font,
    pdf_to_pixel,
    render_structure_tree_text,
    scale_for,
    visualize,
    visualize_debug_dir,
)
from document2chunk.ir import (
    BlockType,
    DocumentMetadata,
    HeadingNode,
    LogicalDocument,
    ParagraphNode,
    Provenance,
    RunNode,
    RunProperties,
    SectionNode,
    SourceType,
)


# ---------------------------------------------------------------------------
# 样式 / 渲染基础设施
# ---------------------------------------------------------------------------


def test_type_colors_cover_blocktypes():
    for bt in BlockType:
        assert bt.value in TYPE_COLORS, f"缺少配色: {bt.value}"


def test_load_font_returns_font():
    f = load_font(12)
    assert f is not None


def test_pdf_to_pixel_and_scale():
    assert pdf_to_pixel([72.0, 144.0, 144.0, 216.0], 2.0) == (144, 288, 288, 432)
    # PDF: dpi=150 → 150/72
    assert abs(scale_for("a.pdf", 150) - 150 / 72.0) < 1e-9
    # 图片：已是像素空间
    assert scale_for("a.png", 150) == 1.0


def test_heading_color_gradient():
    c1 = heading_color(1)
    c9 = heading_color(9)
    # L9 比 L1 更浅（各通道值更大）
    assert all(c9[i] >= c1[i] for i in range(3))


# ---------------------------------------------------------------------------
# BlockNode → element 归一化
# ---------------------------------------------------------------------------


def test_block_to_element_heading():
    h = HeadingNode(
        id="block_000001",
        level=2,
        text="标题",
        runs=[RunNode(id="r1", text="标题", style=RunProperties(font="SimSun", font_size=18.0))],
        provenance=Provenance(source_type=SourceType.PDF, page_index=0, bbox=[10, 20, 30, 40]),
    )
    e = block_to_element(h)
    assert e["type"] == "heading"
    assert e["level"] == 2
    assert e["bbox"] == [10, 20, 30, 40]
    assert e["style"]["size"] == 18.0
    assert e["style"]["font"] == "SimSun"


def test_block_to_element_confidence_and_noprovenance():
    p = ParagraphNode(
        id="b2",
        text="x",
        provenance=Provenance(source_type=SourceType.OCR, page_index=0, bbox=[1, 2, 3, 4], confidence=0.3),
    )
    e = block_to_element(p)
    assert e["type"] == "paragraph"
    assert e["confidence"] == 0.3
    # docx 块无 provenance
    h = HeadingNode(id="b3", level=1, text="t")
    assert block_to_element(h)["bbox"] is None


# ---------------------------------------------------------------------------
# 叠加视图（draw_annotations）
# ---------------------------------------------------------------------------


def test_draw_annotations_shape():
    img = Image.new("RGB", (400, 600), "white")
    elements = [
        {"type": "heading", "level": 1, "bbox": [10, 10, 100, 40], "style": {"size": 18}, "confidence": None},
        {"type": "paragraph", "level": None, "bbox": [10, 50, 300, 80], "style": {}, "confidence": 0.42},
    ]
    out = draw_annotations(img, elements, scale=2.0, header_text="pdf | Page 0")
    # 高度增加统计面板；宽度不变
    assert out.size == (400, 600 + 90)
    assert out.mode == "RGB"


def test_draw_annotations_skips_bboxless():
    img = Image.new("RGB", (200, 200), "white")
    out = draw_annotations(
        img, [{"type": "paragraph", "bbox": None}], scale=1.0, header_text="x"
    )
    assert out.size == (200, 200 + 90)  # 不崩即可


# ---------------------------------------------------------------------------
# 结构树视图（docx 主用）
# ---------------------------------------------------------------------------


def _docx_doc() -> LogicalDocument:
    h = HeadingNode(id="block_000001", level=1, text="第一章")
    p = ParagraphNode(id="block_000002", text="正文段落内容示例文本。")
    root = SectionNode(
        id="sec_root", title="ROOT", level=0,
        subsections=[SectionNode(
            id="sec_000001", title="第一章", level=1,
            heading_node_id="block_000001", block_ids=["block_000002"],
        )],
    )
    return LogicalDocument(
        metadata=DocumentMetadata(source_type=SourceType.DOCX, source_file="a.docx"),
        content=[h, p],
        section_tree=root,
        block_to_section={"block_000001": "sec_000001", "block_000002": "sec_000001"},
    )


def test_structure_tree_text():
    txt = render_structure_tree_text(_docx_doc())
    assert "第一章" in txt
    assert "L1" in txt
    assert "[para]" in txt


def test_visualize_docx_tree_only(tmp_path):
    doc = _docx_doc()
    paths = visualize(doc, out_dir=tmp_path, mode="both")  # docx 无底图 → 降级 tree
    names = {p.name for p in paths}
    assert "structure_tree.png" in names
    assert (tmp_path / "structure_tree.txt").exists()
    # 不应生成叠加图
    assert not any("overlay" in n for n in names)


# ---------------------------------------------------------------------------
# 叠加视图：真实 PDF 底图（PyMuPDF）
# ---------------------------------------------------------------------------


def _make_pdf(path: Path) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 120), "Hello Title", fontsize=20)
    page.insert_text((72, 160), "Body text line.", fontsize=12)
    doc.save(str(path))
    doc.close()


def _pdf_doc() -> LogicalDocument:
    h = HeadingNode(
        id="block_000001", level=1, text="Hello Title",
        provenance=Provenance(source_type=SourceType.PDF, page_index=0, bbox=[70, 100, 220, 125]),
    )
    p = ParagraphNode(
        id="block_000002", text="Body text line.",
        provenance=Provenance(source_type=SourceType.PDF, page_index=0, bbox=[70, 145, 300, 165]),
    )
    root = SectionNode(id="sec_root", title="ROOT", level=0, block_ids=[])
    return LogicalDocument(
        metadata=DocumentMetadata(source_type=SourceType.PDF, source_file="a.pdf", page_count=1),
        content=[h, p],
        section_tree=root,
    )


def test_visualize_pdf_overlay(tmp_path):
    if not has_pymupdf():
        print("跳过（无 PyMuPDF）")
        return
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    doc = _pdf_doc()
    paths = visualize(doc, source_path=pdf, out_dir=tmp_path, mode="overlay", dpi=72)
    names = {p.name for p in paths}
    assert "page_000_overlay.png" in names
    # 叠加图尺寸 = 72dpi 渲染尺寸 + 统计面板
    with Image.open(tmp_path / "page_000_overlay.png") as im:
        w, h = im.size
    assert abs(w - 595) < 2  # 72dpi 下 A4 宽 ~595px
    assert h > 842  # 加了面板


# ---------------------------------------------------------------------------
# 过程模式：debug_dir（与旧库等价）
# ---------------------------------------------------------------------------


def _write_debug_dir(debug_dir: Path) -> None:
    stages = [
        {
            "stage_index": 0, "stage_name": "body_analysis", "stage_type": "global",
            "pages": [{"page_index": 0, "elements": [
                {"type": "paragraph", "bbox": [70, 145, 300, 165], "level": None,
                 "style": {"font": "Helv", "size": 12},
                 "spans": [{"text": "Body text line.", "font": "Helv", "size": 12}]},
            ]}],
        },
        {
            "stage_index": 7, "stage_name": "auto_level", "stage_type": "global",
            "pages": [{"page_index": 0, "elements": [
                {"type": "heading", "bbox": [70, 100, 220, 125], "level": 1,
                 "style": {"font": "Helv", "size": 20}, "heading_confidence": 0.8},
            ]}],
        },
    ]
    debug_dir.mkdir(parents=True, exist_ok=True)
    for s in stages:
        (debug_dir / f"{s['stage_index']:02d}_{s['stage_name']}.json").write_text(
            json.dumps(s, ensure_ascii=False), encoding="utf-8"
        )


def test_visualize_debug_dir(tmp_path):
    if not has_pymupdf():
        print("跳过（无 PyMuPDF）")
        return
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    debug_dir = tmp_path / "debug"
    _write_debug_dir(debug_dir)

    paths = visualize_debug_dir(debug_dir, pdf, out_dir=tmp_path / "viz", dpi=72)
    names = {Path(p).name for p in paths}
    # 每个 stage×page 一张叠加图
    assert any(n.startswith("stage00_body_analysis") for n in names)
    assert any(n.startswith("stage07_auto_level") for n in names)
    # 阶段对比图
    assert (tmp_path / "viz" / "comparison" / "comparison_page000.png").exists()


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


class _Tmp:
    """轻量 tmp_path 替代（免 pytest fixture）。"""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="d2c_viz_"))

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        import shutil

        shutil.rmtree(self.path, ignore_errors=True)


def _run(testfn):
    with _Tmp() as tmp:
        testfn(tmp)


def main():
    test_type_colors_cover_blocktypes()
    test_load_font_returns_font()
    test_pdf_to_pixel_and_scale()
    test_heading_color_gradient()
    test_block_to_element_heading()
    test_block_to_element_confidence_and_noprovenance()
    test_draw_annotations_shape()
    test_draw_annotations_skips_bboxless()
    test_structure_tree_text()
    _run(test_visualize_docx_tree_only)
    _run(test_visualize_pdf_overlay)
    _run(test_visualize_debug_dir)
    print("ALL DEBUG VIZ TESTS PASSED")


if __name__ == "__main__":
    main()
