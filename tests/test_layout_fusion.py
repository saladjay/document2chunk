"""designs/004 版面×span 融合测试 —— image_detection 三信号 + table 校验。

运行：PYTHONPATH="src;tests" python tests/test_layout_fusion.py
"""

from __future__ import annotations

from document2chunk.extractors.pdf import _validate_table
from document2chunk.pipeline.base import PipelineContext
from document2chunk.pipeline.stages.image_detection import ImageDetectionStage
from document2chunk.pipeline.stages.layout_filter import layout_boxes_for_page


def _txt(t, x0, y0, x1, y1):
    return {"type": None, "text": t, "bbox": [x0, y0, x1, y1], "order_index": 0, "spans": []}


def _ctx(images=None, layout=None, w=595, h=842):
    return PipelineContext(
        page_width=w, page_height=h, page_index=0, image_infos=images or [], layout_data=layout
    )


_classify = ImageDetectionStage._classify_image


# ---------- image_detection：背景图 vs 真 figure ----------


def test_background_image_preserves_text():
    """全页背景图 + 多行文字 → 文字保留，不出 image 占位符（首页文字消失修复）。"""
    elems = [_txt(f"line{i}", 72, 100 + i * 20, 300, 115 + i * 20) for i in range(5)]
    bg = [{"image_id": "bg", "bbox": [0, 0, 595, 842]}]
    out = ImageDetectionStage().process(list(elems), _ctx(images=bg))
    assert len(out) == 5, [e.get("type") for e in out]
    assert all(e.get("type") is None for e in out)  # 无 image 占位符
    assert all(e["text"].startswith("line") for e in out)
    print("OK test_background_image_preserves_text")


def test_real_figure_becomes_placeholder():
    """小真图（无文字）→ 出 image 占位符。"""
    elems = [_txt(f"line{i}", 72, 100 + i * 20, 300, 115 + i * 20) for i in range(5)]
    fig = [{"image_id": "fig1", "bbox": [72, 400, 200, 500]}]  # 远离文字
    out = ImageDetectionStage().process(list(elems), _ctx(images=fig))
    types = [e.get("type") for e in out]
    assert "image" in types, types
    print("OK test_real_figure_becomes_placeholder")


# ---------- image_detection：layout 裁决 ----------


def _layout(boxes):
    return [{"result": {"res": {"boxes": boxes}}}]


def test_layout_guided_classification():
    lb = layout_boxes_for_page(
        _layout([
            {"label": "text", "coordinate": [20, 20, 400, 600]},
            {"label": "figure", "coordinate": [400, 400, 560, 560]},
        ]),
        0, 595, 842,
    )
    # 图落在 text 区（小图、无文字）→ skip
    assert _classify([30, 30, 100, 100], [], 595, 842, lb) == "skip"
    # 图落在 figure 区 → figure
    assert _classify([410, 410, 550, 550], [], 595, 842, lb) == "figure"
    # span 主裁判优先于 layout：全页背景 + 文字 → background（即便有 layout）
    elems = [_txt(f"l{i}", 72, 100 + i * 20, 300, 115 + i * 20) for i in range(5)]
    assert _classify([0, 0, 595, 842], elems, 595, 842, lb) == "background"
    print("OK test_layout_guided_classification")


def test_no_layout_small_image_is_figure():
    """无 layout + 小图无文字 → 默认 figure。"""
    assert _classify([72, 400, 200, 500], [], 595, 842, []) == "figure"
    print("OK test_no_layout_small_image_is_figure")


# ---------- table 校验（封面误表修复）----------


def test_table_validation():
    bbox = [72, 100, 300, 160]
    # 1 行"表"（封面误读）→ 降级
    assert _validate_table(bbox, [["标题", "文号"]], None) is False
    # 2×2 真表 → 保留
    assert _validate_table(bbox, [["A", "B"], ["1", "2"]], None) is True
    # 全空 → 降级
    assert _validate_table(bbox, [["", ""], ["", ""]], None) is False
    # 1 行但 layout 有 table 框重叠 → 保留（layout-backed）
    lb = [("table", [72, 100, 300, 120])]
    assert _validate_table([72, 100, 300, 120], [["x"]], lb) is True
    # 列数不足（单列）→ 降级
    assert _validate_table(bbox, [["a"], ["b"]], None) is False
    print("OK test_table_validation")


def main():
    test_background_image_preserves_text()
    test_real_figure_becomes_placeholder()
    test_layout_guided_classification()
    test_no_layout_small_image_is_figure()
    test_table_validation()
    print("\nALL LAYOUT-FUSION TESTS PASSED")


if __name__ == "__main__":
    main()
