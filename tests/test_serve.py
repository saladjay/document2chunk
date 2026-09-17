"""serve（/parse-pdf、CLI 共用路径）测试：docx 路由 + 单次后处理 + .md 直通 + .txt 转录。"""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest

from document2chunk import serve
from document2chunk.exceptions import UnsupportedFormatError
from document2chunk.ir import SourceType

from test_docx import make_docx

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

DOC_XML = (
    f'<w:document xmlns:w="{W}"><w:body>'
    '<w:p><w:r><w:t>测试文档正文段落</w:t></w:r></w:p>'
    "</w:body></w:document>"
)
DOCX_BYTES = make_docx(DOC_XML)

MD_BYTES = "# 测试\n\n正文".encode("utf-8")


def _read_md(zip_bytes: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert "result.md" in z.namelist(), z.namelist()
    return z.read("result.md").decode("utf-8")


def test_parse_to_zip_docx_avoids_pdf_pipeline(monkeypatch):
    """docx 字节不得进 PDF 管线（现状缺陷：PyMuPDF 硬解 docx 产出伪标题垃圾）。"""
    from document2chunk.extractors.pdf import PdfExtractor

    def _boom(self, source, **kw):  # noqa: ANN001
        raise AssertionError("docx 误入 PDF 管线")

    monkeypatch.setattr(PdfExtractor, "extract", _boom)
    md = _read_md(serve.parse_to_zip(DOCX_BYTES, "t.docx"))
    assert "测试文档正文段落" in md


def test_parse_to_files_docx_routes_by_extension(tmp_path):
    """路径模式按 .docx 扩展名路由 → metadata.source_type == DOCX。"""
    src = tmp_path / "t.docx"
    src.write_bytes(DOCX_BYTES)
    out, imgs = tmp_path / "out", tmp_path / "images"
    doc = serve.parse_to_files(str(src), str(out), str(imgs))
    assert (out / "result.md").read_text(encoding="utf-8").find("测试文档正文段落") >= 0
    assert doc.metadata.source_type == SourceType.DOCX


def test_postprocess_runs_exactly_once(monkeypatch):
    """统一后处理全量恰好 1 次（extractor 内部），serve 层不再叠加第二遍。"""
    import document2chunk.postprocess as pp_mod

    calls: list[int] = []
    orig = pp_mod.postprocess

    def _spy(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    monkeypatch.setattr(pp_mod, "postprocess", _spy)
    serve.parse_to_zip(DOCX_BYTES, "t.docx")
    assert len(calls) == 1, f"postprocess 应恰好 1 次，实际 {len(calls)} 次"


def test_parse_to_zip_docx_image_refs_match_files():
    """含图 docx：result.md 引用 images/<媒体名> 且 zip 内确有该文件（上线验收线）。"""
    R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    A = "http://schemas.openxmlformats.org/drawingml/2006/main"
    WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    NS = f'xmlns:w="{W}" xmlns:r="{R}" xmlns:a="{A}" xmlns:wp="{WP}"'
    doc_xml = (
        f'<w:document {NS}><w:body>'
        "<w:p><w:r><w:t>正文</w:t></w:r></w:p>"
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="50"/><wp:docPr descr="logo"/>'
        '<a:graphic><a:blip r:embed="rId7"/></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        "</w:body></w:document>"
    )
    data = make_docx(
        doc_xml,
        media={"word/media/image1.png": b"PNGDATA"},
        rels_xml=(
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId7" Type="{R}/image" Target="media/image1.png"/>'
            "</Relationships>"
        ),
    )
    zip_bytes = serve.parse_to_zip(data, "t.docx")
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    md = z.read("result.md").decode("utf-8")
    assert "](images/image1.png)" in md, md
    assert "images/rId7" not in md, md
    assert "images/image1.png" in z.namelist(), z.namelist()


# ------------------------------------------------------------------
# .md 直通：/parse-pdf 遇 md 文件跳过解析、输出原件
# ------------------------------------------------------------------


def test_parse_to_zip_md_passthrough():
    """zip 模式 .md：已有 # 的内容复原后逐字节不变（无 # 才会重写）。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(MD_BYTES, "t.md")))
    assert z.namelist() == ["result.md"], z.namelist()
    assert z.read("result.md") == MD_BYTES


def test_parse_to_zip_md_uppercase_ext():
    """大写 .MD 扩展名同样走 .md 路径（大小写不敏感）。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(MD_BYTES, "T.MD")))
    assert z.namelist() == ["result.md"], z.namelist()
    assert z.read("result.md") == MD_BYTES


def test_parse_to_zip_md_titles_restored():
    """zip 模式 .md 缺失标题复原：粗体样式行 → ## 标题。"""
    raw = "**某部关于某事项的通知**\n\n**一、总体要求**\n\n（一）指导思想。以某思想为指导，坚持稳中求进工作总基调，推动高质量发展。\n".encode("utf-8")
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(raw, "t.md")))
    md = z.read("result.md").decode("utf-8")
    assert "# 某部关于某事项的通知" in md
    assert "## 一、总体要求" in md
    assert "### （一）指导思想" in md


def test_parse_to_zip_md_undecodable_byte_passthrough():
    """zip 模式 .md 解码失败：回退字节直通（复原失败不影响输出）。"""
    raw = b"\xff\xfe\x00bad"
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(raw, "t.md")))
    assert z.read("result.md") == raw


def test_parse_to_zip_md_gbk_restored():
    """zip 模式 GBK 编码 .md：GB18030 回退解码 + 复原，输出 UTF-8。"""
    raw = "**一、总体要求**\n\n正文内容。\n".encode("gbk")
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(raw, "t.md")))
    md = z.read("result.md").decode("utf-8")
    assert "## 一、总体要求" in md


def test_parse_to_files_md_passthrough(tmp_path):
    """路径模式 .md：已有 # 的内容原字节写盘，返回最小文档（content=[]）。"""
    src = tmp_path / "t.md"
    src.write_bytes(MD_BYTES)
    out, imgs = tmp_path / "out", tmp_path / "images"
    doc = serve.parse_to_files(str(src), str(out), str(imgs))
    assert (out / "result.md").read_bytes() == MD_BYTES
    assert doc.content == []
    assert doc.metadata.source_file == "t.md"


# ------------------------------------------------------------------
# .txt 转录：/parse-pdf 遇 txt 文件解码→UTF-8 无 BOM 后输出 result.md
# ------------------------------------------------------------------

TXT_UTF8 = "第一行中文\nsecond line\n".encode("utf-8")


def test_parse_to_zip_txt_utf8_passthrough():
    """zip 模式 UTF-8 txt：转录后 result.md 与原件逐字节一致。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(TXT_UTF8, "t.txt")))
    assert z.namelist() == ["result.md"], z.namelist()
    assert z.read("result.md") == TXT_UTF8


def test_parse_to_zip_txt_gbk_transcoded():
    """zip 模式 GBK txt：回退 GB18030 解码，result.md 为同文本 UTF-8 字节。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip("测试中文内容".encode("gbk"), "t.txt")))
    assert z.read("result.md") == "测试中文内容".encode("utf-8")


def test_parse_to_zip_txt_bom_stripped():
    """UTF-8 BOM 头剥除：输出无 BOM。"""
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(b"\xef\xbb\xbf" + TXT_UTF8, "t.txt")))
    assert z.read("result.md") == TXT_UTF8


def test_parse_to_zip_txt_undecodable_unsupported():
    """UTF-8 / GB18030 均解码失败 → UnsupportedFormatError。"""
    with pytest.raises(UnsupportedFormatError):
        serve.parse_to_zip(b"\xff\xff\xff", "t.txt")


def test_parse_to_zip_txt_none_filename_unsupported():
    """filename=None 的 bytes 无扩展名依据，不判 txt，维持报错。"""
    with pytest.raises(UnsupportedFormatError):
        serve.parse_to_zip(TXT_UTF8, None)


def test_parse_to_files_txt_transcoded(tmp_path):
    """路径模式 .txt：GBK 转码 + 复原写盘 result.md，返回最小文档（content=[]）。"""
    src = tmp_path / "t.txt"
    src.write_bytes("**一、总体要求**\n\n测试中文内容。\n".encode("gbk"))
    out, imgs = tmp_path / "out", tmp_path / "images"
    doc = serve.parse_to_files(str(src), str(out), str(imgs))
    md = (out / "result.md").read_text(encoding="utf-8")
    assert "## 一、总体要求" in md
    assert "测试中文内容" in md
    assert doc.content == []
    assert doc.metadata.source_file == "t.txt"


def test_parse_to_zip_txt_titles_restored():
    """zip 模式 .txt 复原：独立短样式行 → ## 标题。"""
    raw = "某文件标题已印发。\n\n一、发展现状与形势\n\n取得了一批成果。\n".encode("utf-8")
    z = zipfile.ZipFile(io.BytesIO(serve.parse_to_zip(raw, "t.txt")))
    md = z.read("result.md").decode("utf-8")
    assert "## 一、发展现状与形势" in md


def test_parse_to_zip_md_none_filename_unsupported():
    """filename=None 的 bytes 无扩展名依据，不判 md，维持报错。"""
    with pytest.raises(UnsupportedFormatError):
        serve.parse_to_zip(b"# x", None)


# ------------------------------------------------------------------
# save_dir 输出落盘留痕：/parse-pdf 线上排查（收原件 + 响应 zip + 产物）
# ------------------------------------------------------------------


def test_parse_to_zip_save_dir_writes_artifacts(tmp_path):
    """save_dir 落盘留痕：恰一个「时间戳__净化名」子目录，三件套齐全。

    覆盖 md 直通与 docx 主流程两条返回路径（真实 /parse-pdf 请求）。
    """
    # 分支 1：.md 直通
    md_root = tmp_path / "md"
    md_root.mkdir()
    zip_bytes = serve.parse_to_zip(MD_BYTES, "t.md", save_dir=str(md_root))
    subs = [p for p in md_root.iterdir() if p.is_dir()]
    assert len(subs) == 1, [p.name for p in subs]
    sub = subs[0]
    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}__t\.md", sub.name), sub.name
    assert (sub / "request.bin").read_bytes() == MD_BYTES
    resp = (sub / "response.zip").read_bytes()
    assert resp == zip_bytes
    z = zipfile.ZipFile(io.BytesIO(resp))  # response.zip 是合法 zip
    assert z.read("result.md") == MD_BYTES
    assert (sub / "result.md").read_bytes() == MD_BYTES

    # 分支 2：docx 主流程（走 extractor/postprocess 的完整解析）
    docx_root = tmp_path / "docx"
    docx_root.mkdir()
    zip2 = serve.parse_to_zip(DOCX_BYTES, "t.docx", save_dir=str(docx_root))
    subs2 = [p for p in docx_root.iterdir() if p.is_dir()]
    assert len(subs2) == 1, [p.name for p in subs2]
    sub2 = subs2[0]
    assert re.fullmatch(r"\d{8}-\d{6}-\d{6}__t\.docx", sub2.name), sub2.name
    assert (sub2 / "request.bin").read_bytes() == DOCX_BYTES
    assert (sub2 / "response.zip").read_bytes() == zip2
    assert "测试文档正文段落" in (sub2 / "result.md").read_text(encoding="utf-8")


def test_parse_to_zip_save_dir_failure_not_fatal(tmp_path):
    """save_dir 非法（含 NUL 字节的路径，任何平台必败）：只告警不抛，解析正常返回。"""
    bad_dir = str(tmp_path / "a\x00b")  # embedded null byte → OSError/ValueError
    zip_bytes = serve.parse_to_zip(MD_BYTES, "t.md", save_dir=bad_dir)
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert z.namelist() == ["result.md"]
    assert z.read("result.md") == MD_BYTES


def test_parse_to_zip_save_dir_default_off():
    """不传 save_dir：行为与现状完全一致（无落盘副作用），返回合法 zip。"""
    zip_bytes = serve.parse_to_zip(MD_BYTES, "t.md")
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    assert z.namelist() == ["result.md"]
    assert z.read("result.md") == MD_BYTES


# ---------------------------------------------------------------- timing 打点


@pytest.fixture
def timing_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    monkeypatch.setenv("DOCUMENT2CHUNK_LOG_DIR", str(d))
    return d


def _last_timing(timing_dir):
    files = list(timing_dir.glob("timing-*.jsonl"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])


def test_zip_mode_records_stages(timing_dir):
    serve.parse_to_zip(DOCX_BYTES, "t.docx")
    rec = _last_timing(timing_dir)
    assert rec["status"] == "ok"
    assert rec["file"] == "t.docx"
    assert rec["size_bytes"] == len(DOCX_BYTES)
    assert rec["mode"] == "zip"
    assert rec["source_type"] == "docx"
    for stage in ("detect", "extract", "assemble", "render", "pack"):
        assert stage in rec["stages"], rec["stages"]
    assert "demote" not in rec["stages"]  # demote=False 不出现


def test_demote_stage_recorded_when_enabled(timing_dir):
    serve.parse_to_zip(DOCX_BYTES, "t.docx", demote=True)
    rec = _last_timing(timing_dir)
    assert rec["demote"] is True
    assert "demote" in rec["stages"]


def test_error_records_partial_stages(timing_dir, monkeypatch):
    import pymupdf

    from document2chunk.extractors.pdf import PdfExtractor

    # 本测验证"进程内提取报错→timer 记部分阶段"：关掉 S-2 子进程看门狗走直提
    monkeypatch.setenv("DOCUMENT2CHUNK_EXTRACT_TIMEOUT", "0")

    def _boom(self, source, **kw):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr(PdfExtractor, "extract", _boom)
    # 真 PDF：_extract_with_images 先用 pymupdf 取页几何，坏字节会提前 FileDataError
    # （到不了 extractor）；显式 source_type 跳过 detect 的 pdf_detect，让 RuntimeError 落在 extract 阶段
    _d = pymupdf.open()
    _d.new_page()
    pdf_bytes = _d.tobytes()
    _d.close()
    with pytest.raises(RuntimeError):
        serve.parse_to_zip(pdf_bytes, "bad.pdf", source_type="pdf")
    rec = _last_timing(timing_dir)
    assert rec["status"] == "error"
    assert rec["error_type"] == "RuntimeError"
    assert len(rec["stages"]) >= 1  # 已完成阶段留痕


def test_md_passthrough_status(timing_dir):
    serve.parse_to_zip(MD_BYTES, "t.md")
    rec = _last_timing(timing_dir)
    assert rec["status"] == "passthrough"


def test_txt_passthrough_status(timing_dir):
    """.txt 转录与 .md 直通对称：status=passthrough（ASCII 安全字节避免编码歧义）。"""
    serve.parse_to_zip(b"hello txt", "t.txt")
    rec = _last_timing(timing_dir)
    assert rec["status"] == "passthrough"


def test_legacy_fallback_normalize_stage_recorded(timing_dir, monkeypatch):
    """指纹兜底重跑路径的 timing：legacy 归一化耗时归属 normalize 阶段（第七阶段，仅回退出现）。

    实际触发路径（本机确定触发；转换执行器 mock 掉，不依赖 soffice）：
    错标件 filename=.docx 实为 OLE2 → _run 首跑 extract 阶段解包失败
    （BadZipFile，stage() 的 finally 已记部分耗时）→ 指纹识别 DOC →
    _normalize_legacy 计入 normalize → mock 转换产物 DOCX_BYTES → 二跑 ok。
    """
    from test_format_detect import _fake_ole2

    monkeypatch.setattr(
        serve, "_legacy_convert",
        lambda data, in_ext, target, name=None: DOCX_BYTES,
    )
    md = _read_md(serve.parse_to_zip(_fake_ole2("WordDocument"), "错标.docx"))
    assert "测试文档正文段落" in md  # 确认回退重跑确实走通
    rec = _last_timing(timing_dir)
    assert rec["status"] == "ok"
    assert rec["source_type"] == "docx"
    assert "normalize" in rec["stages"], rec["stages"]
    for stage in ("detect", "extract", "assemble", "render", "pack"):
        assert stage in rec["stages"], rec["stages"]
    # 语义不变：墙钟总耗时 ≥ 各阶段之和（首跑失败耗时 + normalize + 二跑全在内，0.01 容纳逐段取整误差）
    assert rec["total_s"] + 0.01 >= sum(rec["stages"].values()), rec


def test_cli_default_timer(timing_dir, tmp_path):
    src = tmp_path / "in.docx"
    src.write_bytes(DOCX_BYTES)
    serve.cli_main(["--input", str(src), "--output", str(tmp_path / "out"), "--images", str(tmp_path / "img")])
    rec = _last_timing(timing_dir)
    assert rec["mode"] == "cli"
    assert rec["status"] == "ok"
    assert rec["file"] == "in.docx"
