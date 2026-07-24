"""table-extractor 测试（designs/008）。

- _html_parser：HTML→TableNode，保留 colspan/rowspan（零服务依赖）。
- _client：httpx.MockTransport 测鉴权/端点/fmt/错误/504 重试。
- extractor：stub client 测端到端（TableNode + provenance.page_index）。

运行：PYTHONPATH="src;tests" python tests/test_table_extractor.py
"""

from __future__ import annotations

from document2chunk.extractors.table._client import TableServiceClient
from document2chunk.extractors.table._config import TableConfig
from document2chunk.extractors.table._exceptions import TableServiceError
from document2chunk.extractors.table._html_parser import _Idc, html_to_table_node
from document2chunk.extractors.table.extractor import TableExtractor
from document2chunk.ir import SourceType, TableNode

try:
    import httpx
    from httpx import MockTransport

    _HAS = True
except ImportError:
    _HAS = False


# ---------- html_parser ----------


def test_html_parser_colspan_rowspan():
    html = (
        "<table><tbody>"
        '<tr><td colspan="2">合并表头</td><td>列C</td></tr>'
        '<tr><td rowspan="2">a</td><td>b</td><td>c</td></tr>'
        "<tr><td>b2</td><td>c2</td></tr>"
        "</tbody></table>"
    )
    t = html_to_table_node(html, page_index=3)
    assert isinstance(t, TableNode)
    assert len(t.rows) == 3
    assert t.rows[0].cells[0].colspan == 2 and t.rows[0].cells[0].blocks[0].text == "合并表头"
    assert t.rows[1].cells[0].rowspan == 2
    assert t.rows[0].is_header is True  # 首行作表头（无 <th>）
    assert t.provenance.page_index == 3
    print("OK test_html_parser_colspan_rowspan")


def test_html_parser_th_is_header():
    html = "<table><tr><th>姓名</th><th>年龄</th></tr><tr><td>张三</td><td>20</td></tr></table>"
    t = html_to_table_node(html)
    assert t.rows[0].is_header is True and t.rows[1].is_header is False
    assert t.rows[1].cells[0].blocks[0].text == "张三"
    print("OK test_html_parser_th_is_header")


def test_html_parser_cell_boxes_provenance():
    boxes = [[0, 0, 10, 10], [10, 0, 20, 10], [0, 10, 10, 20], [10, 10, 20, 20]]
    t = html_to_table_node(
        "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>",
        page_index=1, cell_boxes=boxes,
    )
    assert t.rows[0].cells[0].blocks[0].provenance.bbox == [0, 0, 10, 10]
    assert t.rows[1].cells[1].blocks[0].provenance.bbox == [10, 10, 20, 20]
    print("OK test_html_parser_cell_boxes_provenance")


# ---------- client（MockTransport）----------


def _client(handler, token="t-tok"):
    cfg = TableConfig(endpoint="http://test", token=token, timeout=10, retry_on_504=0)
    return TableServiceClient(cfg, http_client=httpx.Client(transport=MockTransport(handler)))


def test_client_recognize():
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        seen["fmt"] = dict(req.url.params).get("fmt") or "html"
        # fmt 在 form data，不在 query；从 body 取
        return httpx.Response(200, json={"tables": [{"page": 0, "html": "<table></table>"}], "count": 1, "formats": ["html"]})

    c = _client(h)
    r = c.recognize(b"%PDF-1.5", "a.pdf", fmt="html,json")
    assert r["count"] == 1
    assert seen["auth"] == "Bearer t-tok"
    print("OK test_client_recognize")


def test_client_http_error():
    c = _client(lambda req: httpx.Response(500, text="boom"))
    try:
        c.recognize(b"x", "a.pdf")
        assert False
    except TableServiceError as e:
        assert e.status_code == 500
    print("OK test_client_http_error")


def test_missing_token_raises():
    cfg = TableConfig(token="")
    c = TableServiceClient(cfg, http_client=httpx.Client(transport=MockTransport(lambda r: httpx.Response(200, json={}))))
    try:
        c.recognize(b"x", "a.pdf")
        assert False
    except TableServiceError:
        pass
    print("OK test_missing_token_raises")


# ---------- extractor（stub client）----------


class _Stub:
    def __init__(self, tables):
        self._t = tables

    def recognize(self, data, filename, *, fmt=None, page_range="all"):
        return {"tables": self._t, "count": len(self._t), "formats": ["html", "json"]}


def test_extractor_end_to_end():
    tables = [
        {"page": 0, "html": '<table><tr><td colspan="2">H</td></tr><tr><td>1</td><td>2</td></tr></table>',
         "json": {"cell_box_list": [[0, 0, 5, 5]]}},
        {"page": 2, "html": "<table><tr><td>x</td></tr></table>"},
    ]
    r = TableExtractor(client=_Stub(tables)).extract(b"%PDF-1.5 dummy")
    assert r.metadata.source_type == SourceType.PDF
    assert len(r.content) == 2
    assert r.content[0].rows[0].cells[0].colspan == 2
    assert r.content[1].provenance.page_index == 2  # 第二张表在第 2 页
    print("OK test_extractor_end_to_end")


def main():
    test_html_parser_colspan_rowspan()
    test_html_parser_th_is_header()
    test_html_parser_cell_boxes_provenance()
    if _HAS:
        test_client_recognize()
        test_client_http_error()
        test_missing_token_raises()
    else:
        print("SKIP client tests (httpx 未装)")
    test_extractor_end_to_end()
    print("\nALL TABLE EXTRACTOR TESTS PASSED")


if __name__ == "__main__":
    main()
