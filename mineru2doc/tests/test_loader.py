import json

import pytest

from mineru2doc.loader import FileLoader, MinerULoaderError, _extract_results, _save_http_images, load

CONTENT = [
    {"type": "text", "text": "文档主标题", "text_level": 1, "page_idx": 0},
    {"type": "text", "text": "第一章 总则", "text_level": 2, "page_idx": 0, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "这是正文段落。", "page_idx": 0},
    {"type": "image", "img_path": "images/a.jpg", "img_caption": "图A", "page_idx": 1},
    {"type": "table", "table_body": "<table><tr><td>1</td></tr></table>", "page_idx": 1},
    {"type": "equation", "text": "E=mc^2", "page_idx": 1},
    {"type": "list", "items": ["甲", "乙"], "page_idx": 1},
]


def _write(tmp_path, name="doc_content_list.json"):
    (tmp_path / name).write_text(json.dumps(CONTENT, ensure_ascii=False), encoding="utf-8")


def test_file_loader_from_dir(tmp_path):
    _write(tmp_path)
    doc = FileLoader().load(tmp_path)
    assert len(doc.blocks) == 7
    assert doc.blocks[0].type == "text" and doc.blocks[0].level == 1
    assert doc.blocks[2].level is None           # 正文
    assert doc.blocks[1].bbox == [0.0, 0.0, 10.0, 10.0]
    assert doc.blocks[3].type == "image" and doc.blocks[3].img_path == "images/a.jpg"
    assert doc.blocks[5].latex == "E=mc^2"
    assert doc.blocks[6].items == ["甲", "乙"]


def test_file_loader_from_json_file(tmp_path):
    _write(tmp_path)
    doc = FileLoader().load(tmp_path / "doc_content_list.json")
    assert len(doc.blocks) == 7


def test_load_dispatch_json(tmp_path):
    _write(tmp_path)
    doc = load(tmp_path / "doc_content_list.json")
    assert len(doc.blocks) == 7


def test_pdf_without_base_url_raises():
    with pytest.raises(MinerULoaderError):
        load("x.pdf")


def test_missing_content_list_raises(tmp_path):
    with pytest.raises(MinerULoaderError):
        FileLoader().load(tmp_path)


def test_end_to_end_convert(tmp_path):
    from mineru2doc import convert

    _write(tmp_path)
    md = convert(str(tmp_path))
    assert "# 文档主标题" in md                 # MinerU 标题保留
    assert "## 第一章 总则" in md               # 相对定级：标题H1 之下 第一章→H2
    assert "这是正文段落。" in md
    assert "$$ E=mc^2 $$" in md
    assert "- 甲" in md and "- 乙" in md


# ── md_content / .md 路径（HTTP 实际形态）──

_FILE_PARSE_RESP = {
    "task_id": "abc",
    "status": "completed",
    "results": {"some-file.pdf": {"md_content": "# 主标题\n\n正文。\n\n## 二、子项\n",
                                  "images": {"a.jpg": "aGVsbG8="}}},
}


def test_extract_results():
    md, images = _extract_results(_FILE_PARSE_RESP)
    assert "# 主标题" in md
    assert images == {"a.jpg": "aGVsbG8="}


def test_extract_results_missing():
    with pytest.raises(MinerULoaderError):
        _extract_results({"status": "completed", "results": {"f": {}}})
    with pytest.raises(MinerULoaderError):
        _extract_results({"status": "queued", "results": {}})


def test_file_loader_md_fallback(tmp_path):
    (tmp_path / "doc.md").write_text("# 标题\n\n正文。\n", encoding="utf-8")
    doc = FileLoader().load(tmp_path / "doc.md")
    assert doc.blocks[0].type == "text" and doc.blocks[0].level == 1
    assert doc.blocks[1].level is None


# ── 图片落盘 ──

def test_save_http_images(tmp_path):
    from mineru2doc.model import IMAGE, Block

    blocks = [Block(type=IMAGE, img_path="images/a.jpg")]
    n = _save_http_images(blocks, {"a.jpg": "aGVsbG8="}, str(tmp_path))
    assert n == 1
    saved = (tmp_path / "images" / "a.jpg").read_bytes()  # 保留 md 相对引用
    assert saved == b"hello"


def test_file_loader_copies_images(tmp_path):
    # 源：content_list 引用 images/x.jpg，且文件存在
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "x.jpg").write_bytes(b"PNGDATA")
    (tmp_path / "doc_content_list.json").write_text(
        json.dumps([{"type": "image", "img_path": "images/x.jpg"}], ensure_ascii=False),
        encoding="utf-8")
    out_dir = tmp_path / "out"
    FileLoader().load(tmp_path, image_out_dir=str(out_dir))
    assert (out_dir / "images" / "x.jpg").read_bytes() == b"PNGDATA"


def test_convert_writes_images_next_to_output(tmp_path):
    from mineru2doc import convert

    src = tmp_path / "src"
    (src / "images").mkdir(parents=True)
    (src / "images" / "x.jpg").write_bytes(b"PNGDATA")
    (src / "doc_content_list.json").write_text(
        json.dumps([
            {"type": "text", "text_level": 1, "text": "标题"},
            {"type": "image", "img_path": "images/x.jpg"},
        ], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out" / "res.md"
    convert(str(src), output=str(out))           # image_dir 由 output 推导 = out/
    assert out.exists()
    assert (tmp_path / "out" / "images" / "x.jpg").read_bytes() == b"PNGDATA"
    assert "![](images/x.jpg)" in out.read_text(encoding="utf-8")

