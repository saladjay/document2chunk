"""S-3 O(n²) 热点优化的等价性守卫(issues7 B 类)。

纯优化不改行为 → 以特征化守卫为主:
- merge:手工期望值(多行合并+重复抽取丢份+编号断段+低置信传播)
- is_standalone_line:测试内朴素参考实现逐元素对照
- 三处审查后不改(pdf.py:359 本就同页 / image_detection 页级规模小 / reorder_overlapping
  冒泡回退有视觉顺序语义,等价风险>收益),理由记 issues7。
"""
from __future__ import annotations

from document2chunk.pipeline.heading_scorer import _SAME_LINE_TOLERANCE, is_standalone_line
from document2chunk.pipeline.stages.merge import MergeStage


def _para(text, y, *, level=1, size=10.0, low=False, x0=50.0, x1=500.0):
    bbox = [x0, y, x1, y + 12]
    el = {
        "type": "paragraph", "text": text, "markdown": text,
        "bbox": bbox, "level": level,
        "style": {"size": size, "font": "F"},
        "spans": [{"text": text, "bbox": list(bbox)}],
    }
    if low:
        el["low_confidence"] = True
    return el


def test_guard_merge_accumulation_equivalence():
    """合并语义逐字段守卫:拼接、重复抽取丢一份、编号断段、低置信传播、bbox 并、spans 并。"""
    elems = [
        _para("第一行", 100.0),
        _para("第一行", 100.0),               # 紧跟原始行 → 重复抽取 → 丢一份
        _para("第二行", 114.0),
        _para("1. 新列表项", 128.0),          # 编号开头 → 断段
        _para("低置信行", 142.0, low=True),   # 低置信 → 传播到所在段
        _para("第六行", 156.0),
    ]
    out = MergeStage().process([dict(e) for e in elems], None)
    texts = [b["text"] for b in out]
    assert texts == ["第一行第二行", "1. 新列表项低置信行第六行"]
    marks = [b.get("low_confidence") for b in out]
    assert marks == [None, True]
    # spans 数 = 2/3(重复抽取那份丢弃)
    assert [len(b["spans"]) for b in out] == [2, 3]
    # markdown 与 text 同步拼接
    assert [b["markdown"] for b in out] == texts
    # bbox 取并
    assert out[0]["bbox"] == [50.0, 100.0, 500.0, 126.0]


def _ref_standalone(elem, elems, pw, ph):
    """朴素参考实现(与优化前语义逐条对应)。"""
    bbox = elem.get("bbox")
    if not bbox or len(bbox) < 4:
        return False
    y0, y1 = bbox[1], bbox[3]
    x0, x1 = bbox[0], bbox[2]
    cy = (y0 + y1) / 2
    if ph > 0 and (cy < ph * 0.08 or cy > ph * 0.92):
        return False
    if pw > 0 and (x1 - x0) > pw * 0.65:
        return False
    for o in elems:
        if o is elem:
            continue
        ob = o.get("bbox")
        if not ob or len(ob) < 4:
            continue
        if abs(cy - (ob[1] + ob[3]) / 2) <= _SAME_LINE_TOLERANCE:
            return False
    return True


def test_guard_is_standalone_line_reference_equivalence():
    """优化实现与朴素参考实现在一个确定性合成页上逐元素一致。"""
    pw, ph = 595.0, 842.0
    elements = []
    y = 90.0
    # 30 行,每行 1-3 个元素;穿插缺 bbox / 页脚区 / 超宽元素
    for row in range(30):
        n = 1 + row % 3
        for k in range(n):
            x0 = 50.0 + k * 180.0
            elements.append(_para(f"r{row}c{k}", y, x0=x0, x1=x0 + 150.0))
        if row == 7:
            elements.append({"type": "paragraph", "text": "no-bbox"})
        if row == 15:
            elements.append(_para("超宽", y, x0=10.0, x1=580.0))
        y += 22
    elements.append(_para("页脚区", 800.0))
    elements.append(_para("页眉区", 30.0))

    # 构造页级预排序索引(与 classification.process 的接线一致)
    sorted_center_ys = sorted(
        ((e["bbox"][1] + e["bbox"][3]) / 2, i)
        for i, e in enumerate(elements)
        if e.get("bbox") and len(e["bbox"]) >= 4
    )
    for i, elem in enumerate(elements):
        fast = is_standalone_line(
            elem, elements, pw, ph, sorted_center_ys=sorted_center_ys, elem_pos=i,
        )
        ref = _ref_standalone(elem, elements, pw, ph)
        assert fast == ref, f"元素 {i}({elem.get('text')}) fast={fast} ref={ref}"

    # 未传预排序的老调用路径仍可用(库级兼容)
    for elem in elements[:5]:
        assert is_standalone_line(elem, elements, pw, ph) == _ref_standalone(
            elem, elements, pw, ph
        )
