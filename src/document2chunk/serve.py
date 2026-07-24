"""serve —— /parse-pdf 接口 + CLI（Chai 复杂解析对接，designs 对接文档）。

三种模式（与 /parse 契约一致，仅产物形式不同）：
- 路径模式：收 file_path/output_dir/image_dir → 写 result.md + images/，返回 {"status":"ok"}
- zip 模式：收文件二进制 → 返回 zip 流（result.md + images/）
- CLI：python -m document2chunk cli --input X --output Y --images Z → 写 result.md + images/

result.md 为**全文**（主文 + 附件），图片引用相对路径 images/xxx.png。
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional, Union

from document2chunk.api import _assemble, _pdf_kind, _source_name
from document2chunk.ir import ImageNode, LogicalDocument, SourceType

_DEMOTE_MAX_HEADING_LEN = 60  # demote：标题文本超此且以句号结尾 → 降为正文


def _extract_with_images(source, st: SourceType, image_dir: Optional[str]):
    """按源类型用对应 extractor 提取（图片落 image_dir）。"""
    if st == SourceType.OCR:
        from document2chunk.extractors.ocr import OcrExtractor
        return OcrExtractor().extract(source, image_out_dir=image_dir)
    from document2chunk.extractors.pdf import PdfExtractor
    return PdfExtractor(image_dir=image_dir).extract(source)


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
) -> LogicalDocument:
    """路径模式：解析 source，写 output_dir/result.md + image_dir/ 图片。"""
    from document2chunk.api import _route_source_type

    output_dir = Path(output_dir)
    image_dir = Path(image_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    st = _route_source_type(source, source_type) if source_type else _pdf_kind(source)
    result = _extract_with_images(source, st, str(image_dir))
    doc = _assemble(result, False)

    name = _source_name(source)
    if doc.metadata.source_file is None and name:
        doc.metadata.source_file = name
    if doc.metadata.source_type is None:
        doc.metadata.source_type = st

    if demote:
        _apply_demote(doc)
    _prefix_image_ids(doc, image_dir.name + "/")
    (output_dir / "result.md").write_text(_doc_markdown(doc), encoding="utf-8")
    return doc


def parse_to_zip(
    data: bytes,
    filename: Optional[str] = None,
    *,
    image_dir_name: str = "images",
    demote: bool = False,
    source_type: Any = None,  # noqa: F821
) -> bytes:
    """zip 模式：解析 data，返回 zip 字节流（根目录 result.md + images/）。"""
    from document2chunk.api import _route_source_type

    tmp = Path(tempfile.mkdtemp(prefix="d2c_zip_"))
    try:
        image_dir = tmp / image_dir_name
        st = _route_source_type(data, source_type) if source_type else _pdf_kind(data)
        result = _extract_with_images(data, st, str(image_dir))
        doc = _assemble(result, False)
        if filename and doc.metadata.source_file is None:
            doc.metadata.source_file = filename
        if doc.metadata.source_type is None:
            doc.metadata.source_type = st
        if demote:
            _apply_demote(doc)
        _prefix_image_ids(doc, image_dir_name + "/")
        md = _doc_markdown(doc)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("result.md", md)
            if image_dir.exists():
                for img in sorted(image_dir.rglob("*")):
                    if img.is_file():
                        arc = img.relative_to(tmp).as_posix()  # images/xxx.png
                        zf.write(img, arc)
        return buf.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
