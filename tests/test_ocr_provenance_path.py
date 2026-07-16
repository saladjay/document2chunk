"""回归：OCR provenance 路径修复（extractor 从 prunedResult.parsing_res_list 取 bbox）。

真实 PaddleOCR 服务把 parsing_res_list / width / height 放在
``layoutParsingResults[0].prunedResult`` 下，而非顶层。早期代码读顶层 → bbox=None。
此测试用两种响应形态（顶层 + prunedResult）验证 extractor 都能拿到 bbox。

运行：PYTHONPATH="src;tests" python tests/test_ocr_provenance_path.py
"""

from __future__ import annotations

import copy

from document2chunk.extractors.ocr import OcrExtractor
from document2chunk.ir import SourceType

MD = "# 标题\n\n正文段落。"
PRL = [
    {"block_label": "doc_title", "block_content": "# 标题", "block_bbox": [10, 10, 100, 30], "block_order": 1},
    {"block_label": "text", "block_content": "正文段落。", "block_bbox": [10, 40, 100, 60], "block_order": 2},
]


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def active_model(self):
        return "vl"

    def parse(self, media, filename, *, model):
        return copy.deepcopy(self._resp)


def _resp_top_level():
    """合成 fixture 形态：parsing_res_list 在 lpr 顶层。"""
    return {
        "markdown": MD,
        "images": {},
        "layoutParsingResults": [{"page_index": 1, "parsing_res_list": PRL}],
    }


def _resp_pruned_result():
    """真实服务形态：parsing_res_list / width / height 在 prunedResult 下。"""
    return {
        "markdown": MD,
        "images": {},
        "layoutParsingResults": [
            {"prunedResult": {"width": 1000, "height": 1000, "parsing_res_list": PRL}}
        ],
    }


def _bbox_count(result):
    return sum(1 for b in result.content if b.provenance and b.provenance.bbox)


def test_provenance_from_top_level():
    r = OcrExtractor(client=_FakeClient(_resp_top_level())).extract(b"FAKE")
    assert r.metadata.source_type == SourceType.OCR
    assert _bbox_count(r) >= 1, [b.provenance for b in r.content]
    print("OK test_provenance_from_top_level")


def test_provenance_from_pruned_result():
    """真实服务形态（回归本修复）：bbox 必须从 prunedResult.parsing_res_list 取到。"""
    r = OcrExtractor(client=_FakeClient(_resp_pruned_result())).extract(b"FAKE")
    assert _bbox_count(r) >= 1, "bbox 未从 prunedResult.parsing_res_list 取到"
    # 抽样：至少一个块的 bbox 来自 PRL
    boxes = [b.provenance.bbox for b in r.content if b.provenance and b.provenance.bbox]
    assert any(b == [10.0, 10.0, 100.0, 30.0] or b == [10.0, 40.0, 100.0, 60.0] for b in boxes), boxes
    print("OK test_provenance_from_pruned_result")


if __name__ == "__main__":
    test_provenance_from_top_level()
    test_provenance_from_pruned_result()
    print("ALL PROVENANCE-PATH TESTS PASSED")
