"""serve —— /parse-pdf 接口 + CLI（Chai 复杂解析对接，designs 对接文档）。

三种模式（与 /parse 契约一致，仅产物形式不同）：
- 路径模式：收 file_path/output_dir/image_dir → 写 result.md + images/，返回 {"status":"ok"}
- zip 模式：收文件二进制 → 返回 zip 流（result.md + images/）
- CLI：python -m document2chunk cli --input X --output Y --images Z → 写 result.md + images/

全类型路由（pdf/docx/图片，按扩展名或魔数）；后处理由各 extractor 内部统一执行
（designs/009），serve 层不再叠加。result.md 为**全文**（主文 + 附件）。
例外早退：.md 解码（UTF-8 优先 / GB18030 回退 / 剥 BOM，失败回退字节直通）、
.txt 转录（同前），两者都过 text_titles.restore_titles 复原缺失标题后输出。

可观测性：timer（document2chunk.timing.StageTimer）可选传入；缺省自建（CLI 自动记录）。
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from document2chunk.api import _assemble, _source_name
from document2chunk.timing import StageTimer, reset_current_timer, set_current_timer
from document2chunk.exceptions import Document2ChunkError, UnsupportedFormatError
from document2chunk.ir import (
    DocumentMetadata,
    ImageNode,
    LogicalDocument,
    SectionNode,
    SourceType,
)

_DEMOTE_MAX_HEADING_LEN = 60  # demote：标题文本超此且以句号结尾 → 降为正文

logger = logging.getLogger(__name__)

# 落盘留痕目录名净化：仅留字母数字/中文/./-/_（\w unicode 覆盖中文），其余替换 _
_SAVE_NAME_BAD_CHARS = re.compile(r"[^\w.\-]")
_SAVE_NAME_MAX_LEN = 80


def _sanitize_save_name(name: Optional[str]) -> str:
    """落盘子目录名净化：非法字符换 _，截断 80 字符，空名 → unnamed。"""
    cleaned = _SAVE_NAME_BAD_CHARS.sub("_", name or "")[:_SAVE_NAME_MAX_LEN]
    return cleaned or "unnamed"


def _save_zip_artifacts(
    save_dir: str, filename: Optional[str], data: bytes, zip_bytes: bytes
) -> None:
    """输出落盘留痕（线上排查 Chai 对接差异）：

    save_dir/<时间戳__净化名>/ 下写 request.bin（收到的原始字节）、
    response.zip（返回的 zip 字节）、以及从 zip 解包的 result.md + images/。
    任何失败只 log warning，绝不影响解析主流程。
    """
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        req_dir = Path(save_dir) / f"{ts}__{_sanitize_save_name(filename)}"
        req_dir.mkdir(parents=True, exist_ok=True)
        (req_dir / "request.bin").write_bytes(bytes(data))
        (req_dir / "response.zip").write_bytes(zip_bytes)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target = req_dir / info.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
    except Exception as e:  # noqa: BLE001 —— 留痕失败绝不上抛
        logger.warning(
            "输出落盘留痕失败（不影响解析）save_dir=%s name=%r: %s: %s",
            save_dir, filename, type(e).__name__, e,
        )


def _is_md_name(name: Optional[str]) -> bool:
    """文件名是否 .md 扩展名（大小写不敏感）—— /parse-pdf 直通判定依据。"""
    return bool(name) and Path(name).suffix.lower() == ".md"


def _is_txt_name(name: Optional[str]) -> bool:
    """文件名是否 .txt 扩展名（大小写不敏感）—— /parse-pdf 转录判定依据。"""
    return bool(name) and Path(name).suffix.lower() == ".txt"


def _decode_text(data: bytes) -> Optional[str]:
    """解码：UTF-8 优先、GB18030（GBK 超集）回退，剥 BOM；失败返回 None。"""
    text = None
    for enc in ("utf-8", "gb18030"):
        try:
            text = bytes(data).decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return None
    if text.startswith("﻿"):  # 剥离开头 UTF-8 BOM（decode 后残留为首字符）
        text = text[1:]
    return text


def _restore_text(text: str, name: Optional[str]) -> str:
    """标题复原（text_titles.restore_titles）；任何异常回退原文，绝不影响解析。"""
    try:
        from document2chunk.text_titles import restore_titles

        return restore_titles(text)
    except Exception as e:  # noqa: BLE001 —— 复原失败回退原文
        logger.warning("标题复原失败（回退原文）name=%r: %s: %s", name, type(e).__name__, e)
        return text


def _txt_to_utf8(data: bytes) -> bytes:
    """txt 转录：解码 + 标题复原，统一输出 UTF-8 无 BOM。"""
    text = _decode_text(data)
    if text is None:
        raise UnsupportedFormatError("txt 转录失败：内容既非 UTF-8 也非 GB18030")
    return _restore_text(text, None).encode("utf-8")


# ---- 老格式归一化（spec docs/superpowers/specs/2026-09-01-legacy-format-support） ----
_LEGACY_EXTS = {".doc", ".rtf", ".wps", ".ppt", ".pptx"}  # 后缀即进转换分支
_SPREADSHEET_EXTS = {".xls", ".xlsx", ".xlsm", ".et"}  # 表格：二期，明确 400
# 打开·解包期异常（UnsupportedFormat/InvalidDocx/InvalidSource 都是 Document2ChunkError
# 子类）；BadZipFile 是 DocxExtractor 解包非 zip 输入（错标 .docx 实为 OLE2）抛的裸异常
_FORMAT_EXC_TYPES = (Document2ChunkError, zipfile.BadZipFile)


# 转换的间接引用点（测试 mock 用；包一层避免 monkeypatch 打不到惰性 import）
def _legacy_convert(data, in_ext, target, name=None):
    from document2chunk import legacy_convert
    logger.info("老格式归一化: %s %s→%s", name or "(未命名)", in_ext, target)
    return legacy_convert.convert(data, in_ext, target, name=name)


def _doc_target_ext() -> str:
    t = os.environ.get("DOCUMENT2CHUNK_DOC_TARGET", "docx").strip().lower()
    return ".pdf" if t == "pdf" else ".docx"


def _xls_err(name: Optional[str], kind: str) -> UnsupportedFormatError:
    return UnsupportedFormatError(
        f"检测到表格格式（{kind}）：{name or '(未命名)'}——即将支持；"
        "请先导出为 PDF/docx 后上传"
    )


def _normalize_legacy(data: bytes, name: Optional[str], *, exc_prefix: str = "") -> tuple[bytes, str]:
    """指纹识别 → 归一化。返回 (产物 bytes, 归位后的规范名)。

    - XLS/XLSX → 400 即将支持
    - UNKNOWN → 400 + 诊断（exc_prefix 非空时拼原异常前缀）
    - DOC/RTF/WPS → 转换为 _doc_target_ext()；PPT/PPTX → 转换为 .pdf
    - 纯 zip → 400 明确报错；zip 家族成员（docx/pptx/xlsx，含反向错标）识别为
      具体 kind 后走对应分支：DOCX/IMAGE/PDF 可解析 kind → 原样返回不转换
      （路由层按 PK 嗅探/魔数自行归位）
    """
    from document2chunk.format_detect import FileKind, diagnose, identify

    d = identify(data)
    kind = d.kind
    if kind in (FileKind.XLS, FileKind.XLSX):
        raise _xls_err(name, kind.value)
    if kind is FileKind.UNKNOWN:
        head = f"{exc_prefix}——" if exc_prefix else ""
        raise UnsupportedFormatError(f"{head}无法识别的文件格式——{diagnose(data, name)}")
    if kind is FileKind.ZIP:
        raise UnsupportedFormatError(
            f"检测到普通 zip 压缩包：{name or '(未命名)'}——请解压后上传其中的文档"
        )
    if kind in (FileKind.PPT, FileKind.PPTX):
        target = ".pdf"
    elif kind in (FileKind.DOC, FileKind.RTF, FileKind.WPS):
        target = _doc_target_ext()
    else:  # PDF / DOCX / IMAGE：内容可解析，原异常另有原因——原样返回
        return data, name or (f"input{d.ext}" if d.ext else "input.bin")
    product = _legacy_convert(data, d.ext, target, name)
    return product, (Path(name or "f").stem + target)


def _needs_legacy(name: Optional[str]) -> bool:
    """后缀决策：True=老格式直转分支；另有表格后缀在此抛 400。"""
    suffix = Path(name).suffix.lower() if name else ""
    if suffix in _SPREADSHEET_EXTS:
        raise _xls_err(name, suffix.lstrip("."))
    return suffix in _LEGACY_EXTS


def _prepare(source, name_hint: Optional[str]) -> tuple[object, Optional[str]]:
    """统一入口：后缀快路径 + 老格式直转。返回 (新source, 新name)。

    bytes/路径两模式共用同一决策（先取 bytes 再识别）；现代格式原样返回零扰动。
    """
    if isinstance(source, (bytes, bytearray)):
        name, data = name_hint, bytes(source)
        if _needs_legacy(name):
            return _normalize_legacy(data, name)
        return source, name
    p = Path(source)
    name = name_hint or p.name
    if not _needs_legacy(name):
        return source, name
    return _normalize_legacy(p.read_bytes(), name)



def _minimal_doc(name: Optional[str]) -> LogicalDocument:
    """直通/转录早退共用：不进 extractor/postprocess 的最小空文档。"""
    return LogicalDocument(
        metadata=DocumentMetadata(source_file=name),
        content=[],
        section_tree=SectionNode(id="sec_root", title="ROOT", level=0),
    )


def _extract_with_images(source, st: SourceType, image_dir: Optional[str]):
    """按源类型用对应 extractor 提取（图片落 image_dir）。返回 (result, page_geometry)。"""
    if st == SourceType.OCR:
        from document2chunk.extractors.ocr import OcrExtractor
        from document2chunk.extractors.ocr._chunker import iter_pages, page_count
        data = source if isinstance(source, (bytes, bytearray)) else open(source, "rb").read()
        pc = page_count(data)
        geo = {}
        for pi, _media, _fname, pw, ph in iter_pages(data, "src"):
            geo[pi] = (pw, ph)
            if pi + 1 >= pc:
                break
        return OcrExtractor().extract(source, image_out_dir=image_dir), geo
    if st == SourceType.DOCX:
        from document2chunk.extractors.docx import DocxExtractor
        # DOCX 无页几何，page_geometry=None；extract() 内部自跑统一 postprocess
        return DocxExtractor().extract(source, image_dir=image_dir), None
    from document2chunk.extractors.pdf import PdfExtractor
    # PDF 页面尺寸
    import pymupdf as _fitz
    if isinstance(source, (bytes, bytearray)):
        _doc = _fitz.open(stream=bytes(source), filetype="pdf")
    else:
        _doc = _fitz.open(str(source))
    geo = {i: (_doc[i].rect.width, _doc[i].rect.height) for i in range(len(_doc))}
    _doc.close()
    return PdfExtractor(image_dir=image_dir).extract(source), geo


def _walk_blocks(doc: LogicalDocument):
    """遍历 doc.content + 各 attachment.content 的所有块。"""
    for b in doc.content:
        yield b
    for att in getattr(doc, "attachments", []) or []:
        for b in att.content:
            yield b


def _prefix_image_ids(doc: LogicalDocument, prefix: str) -> None:
    """ImageNode.image_id 加 images/ 前缀（result.md 相对路径引用）。"""
    if not prefix:
        return
    for b in _walk_blocks(doc):
        if isinstance(b, ImageNode) and b.image_id and not b.image_id.startswith(prefix):
            # 去掉可能的前导 ./ 或 /
            iid = b.image_id.lstrip("./").lstrip("/")
            b.image_id = prefix + iid


def _apply_demote(doc: LogicalDocument) -> None:
    """demote=true：把误判的长句标题（>阈值 + 以句号结尾）降为正文。"""
    from document2chunk.ir import HeadingNode, ParagraphNode

    for att in [doc, *getattr(doc, "attachments", [])]:
        new = []
        for b in att.content:
            if isinstance(b, HeadingNode):
                t = (b.text or "").strip()
                if len(t) > _DEMOTE_MAX_HEADING_LEN and t and t[-1] in "。！？.!?":
                    new.append(ParagraphNode(
                        id=b.id, text=b.text, runs=getattr(b, "runs", []),
                        provenance=b.provenance, metadata=b.metadata,
                    ))
                    continue
            new.append(b)
        att.content = new


def _doc_markdown(doc: LogicalDocument) -> str:
    """全文 markdown（主文 + 附件拼接）。"""
    from document2chunk.export import to_markdown
    parts = [to_markdown(doc)]
    for att in getattr(doc, "attachments", []) or []:
        parts.append("\n\n" + to_markdown(att))
    return "".join(parts)


def parse_to_files(
    source: Union[str, Path, bytes],
    output_dir: Union[str, Path],
    image_dir: Union[str, Path],
    *,
    demote: bool = False,
    source_type: Any = None,  # noqa: F821
    timer: Optional[StageTimer] = None,
) -> LogicalDocument:
    """路径模式：解析 source，写 output_dir/result.md + image_dir/ 图片。

    timer 缺省时自建（CLI 路径自动获得可观测性）。
    """
    from document2chunk.api import _route_source_type

    output_dir = Path(output_dir)
    image_dir = Path(image_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    timer = timer or StageTimer(file=_source_name(source), mode="cli", demote=demote)
    token = set_current_timer(timer)

    try:
        source, name = _prepare(source, None)

        if _is_md_name(name):
            # .md 早退：解码→标题复原→UTF-8 无 BOM 写 result.md；解码失败回退字节直通
            raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
            text = _decode_text(raw)
            out = _restore_text(text, name).encode("utf-8") if text is not None else raw
            (output_dir / "result.md").write_bytes(out)
            timer.finish("passthrough")
            return _minimal_doc(name)

        if _is_txt_name(name):
            # .txt 转录：解码规范化为 UTF-8 无 BOM + 标题复原，写 result.md
            raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
            (output_dir / "result.md").write_bytes(_txt_to_utf8(raw))
            timer.finish("passthrough")
            return _minimal_doc(name)

        def _run(src) -> LogicalDocument:
            # 主/兜底共用的组装段（提取避免复制）：路由→提取→组装→元数据→写盘
            with timer.stage("detect"):
                st = _route_source_type(src, source_type)
            timer.source_type = st.value
            with timer.stage("extract"):
                result, geo = _extract_with_images(src, st, str(image_dir))
            with timer.stage("assemble"):
                doc = _assemble(result, False)

            if doc.metadata.source_file is None and name:
                doc.metadata.source_file = name
            if doc.metadata.source_type is None:
                doc.metadata.source_type = st

            if demote:
                with timer.stage("demote"):
                    _apply_demote(doc)
            _prefix_image_ids(doc, image_dir.name + "/")
            with timer.stage("render"):
                md = _doc_markdown(doc)
            with timer.stage("pack"):
                (output_dir / "result.md").write_text(md, encoding="utf-8")
            return doc

        try:
            doc = _run(source)
        except _FORMAT_EXC_TYPES as exc:
            # 指纹兜底：打开/解包期失败 → 按内容识别归一化（转换/归位/400）后重跑
            raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
            source, name = _normalize_legacy(raw, name, exc_prefix=str(exc))
            doc = _run(source)
        timer.finish("ok")
        return doc
    except Exception as exc:  # noqa: BLE001
        timer.finish("error", error_type=type(exc).__name__)
        raise
    finally:
        reset_current_timer(token)


def parse_to_zip(
    data: bytes,
    filename: Optional[str] = None,
    *,
    image_dir_name: str = "images",
    demote: bool = False,
    source_type: Any = None,  # noqa: F821
    save_dir: Optional[str] = None,
    timer: Optional[StageTimer] = None,
) -> bytes:
    """zip 模式：解析 data，返回 zip 字节流（根目录 result.md + images/）。

    save_dir 非空时落盘留痕（request.bin + response.zip + 解包产物），失败仅告警。
    老格式（.doc/.rtf/... 后缀直转，或解析失败后指纹兜底）先归一化为 docx/pdf。
    timer 缺省时自建。
    """
    from document2chunk.api import _route_source_type

    timer = timer or StageTimer(file=filename, size_bytes=len(data), mode="zip", demote=demote)
    token = set_current_timer(timer)

    try:
        source, name = _prepare(data, filename)

        if _is_md_name(name):
            # .md 早退：解码→标题复原→UTF-8 无 BOM 打包；解码失败回退原始字节直通
            text = _decode_text(source)
            out = _restore_text(text, name).encode("utf-8") if text is not None else source
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("result.md", out)
            zip_bytes = buf.getvalue()
            if save_dir:
                _save_zip_artifacts(save_dir, name, data, zip_bytes)
            timer.finish("passthrough")
            return zip_bytes

        if _is_txt_name(name):
            # .txt 转录：不进 extractor/postprocess，转 UTF-8 无 BOM + 标题复原后打包
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("result.md", _txt_to_utf8(source))
            zip_bytes = buf.getvalue()
            if save_dir:
                _save_zip_artifacts(save_dir, name, data, zip_bytes)
            timer.finish("passthrough")
            return zip_bytes

        tmp = Path(tempfile.mkdtemp(prefix="d2c_zip_"))
        try:
            image_dir = tmp / image_dir_name

            def _run(src) -> bytes:
                # 主/兜底共用的组装打包段（提取避免复制）：路由→提取→组装→元数据→打包
                with timer.stage("detect"):
                    st = _route_source_type(src, source_type)
                timer.source_type = st.value
                with timer.stage("extract"):
                    result, geo = _extract_with_images(src, st, str(image_dir))
                with timer.stage("assemble"):
                    doc = _assemble(result, False)
                if name and doc.metadata.source_file is None:
                    doc.metadata.source_file = name
                if doc.metadata.source_type is None:
                    doc.metadata.source_type = st
                if demote:
                    with timer.stage("demote"):
                        _apply_demote(doc)
                _prefix_image_ids(doc, image_dir_name + "/")
                with timer.stage("render"):
                    md = _doc_markdown(doc)

                with timer.stage("pack"):
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        zf.writestr("result.md", md)
                        if image_dir.exists():
                            for img in sorted(image_dir.rglob("*")):
                                if img.is_file():
                                    arc = img.relative_to(tmp).as_posix()  # images/xxx.png
                                    zf.write(img, arc)
                    zip_bytes = buf.getvalue()
                    if save_dir:
                        # 留痕收「请求原件」：request.bin 用收到的原始 data（非转换产物）
                        _save_zip_artifacts(save_dir, name, data, zip_bytes)
                return zip_bytes

            try:
                zip_bytes = _run(source)
            except _FORMAT_EXC_TYPES as exc:
                # 指纹兜底：打开/解包期失败 → 按内容识别归一化（转换/归位/400）后重跑
                raw = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
                source, name = _normalize_legacy(raw, name, exc_prefix=str(exc))
                zip_bytes = _run(source)
            timer.finish("ok")
            return zip_bytes
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        timer.finish("error", error_type=type(exc).__name__)
        raise
    finally:
        reset_current_timer(token)


def cli_main(argv: Optional[list] = None) -> int:
    """CLI：python -m document2chunk cli --input X --output Y --images Z。"""
    import argparse

    parser = argparse.ArgumentParser(prog="document2chunk cli", description="解析文件 → result.md + images/")
    parser.add_argument("--input", required=True, help="原始文件路径")
    parser.add_argument("--output", required=True, help="产物目录（写 result.md）")
    parser.add_argument("--images", required=True, help="图片目录")
    parser.add_argument("--demote", action="store_true", help="降误检（长句标题降为正文）")
    args = parser.parse_args(argv)

    try:
        parse_to_files(args.input, args.output, args.images, demote=args.demote)
        print(f"[ok] result.md -> {args.output}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[err] {type(e).__name__}: {e}", flush=True)
        return 1
