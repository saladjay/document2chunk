"""api —— 库入口 :func:`parse` + FastAPI ``/parse``（集成层）。

架构：``designs/001-target-architecture.md`` §9；握手契约：``openspec/INTEGRATION.md`` §6。

数据流：源路由 → extractor.extract() → structure.assemble() → :class:`LogicalDocument`。

extractor / structure / export 由其它 session 提供并通过**惰性导入**接入；未就绪时
抛 :class:`MissingDependencyError`。联调前可用 :func:`register_extractor` 注入 mock
extractor 跑通路由骨架。

注：本模块**不**使用 ``from __future__ import annotations``——FastAPI 需在定义期
即时解析 ``request: Request`` 注解（``Request`` 在 ``create_app`` 闭包内导入）。
"""

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict

from document2chunk import __version__
from document2chunk.exceptions import (
    Document2ChunkError,
    MissingDependencyError,
    UnsupportedFormatError,
)
from document2chunk.ir import ExtractionResult, LogicalDocument, SourceType

log = logging.getLogger(__name__)

Source = Union[str, Path, bytes, bytearray]

# 路由用扩展名
_PDF_EXT = ".pdf"
_DOCX_EXT = ".docx"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif", ".webp"}

# source_type → 推荐安装的 extra
_EXTRA_FOR: Dict[SourceType, str] = {
    SourceType.PDF: "pdf",
    SourceType.OCR: "ocr",
    SourceType.DOCX: "docx",
}


# ---------------------------------------------------------------------------
# ParseOptions
# ---------------------------------------------------------------------------


class ParseOptions(BaseModel):
    """解析选项（透传给 extractor）。extra 字段允许各 extractor 自定义配置。"""

    model_config = ConfigDict(extra="allow")

    dpi: int = 150
    extract_tables: bool = True
    extract_images: bool = True
    # designs/009：表格 → 高清截图嵌入 markdown。table_image=True 且提供 image_dir 时生效；
    # 无 image_dir 则不截图，markdown 自动回退表格（现行行为不变）。
    table_image: bool = True
    table_image_dpi: int = 300
    deskew: bool = True


# ---------------------------------------------------------------------------
# Extractor 协议与注册表
# ---------------------------------------------------------------------------


@runtime_checkable
class Extractor(Protocol):
    """extractor 统一接口（INTEGRATION §2）。"""

    source_type: SourceType

    def extract(
        self,
        source: Source,
        *,
        options: Optional[ParseOptions] = None,
    ) -> ExtractionResult: ...


def _load_pdf_extractor() -> Extractor:
    from document2chunk.extractors.pdf import PdfExtractor  # type: ignore

    return PdfExtractor()


def _load_docx_extractor() -> Extractor:
    from document2chunk.extractors.docx import DocxExtractor  # type: ignore

    return DocxExtractor()


def _load_ocr_extractor() -> Extractor:
    from document2chunk.extractors.ocr import OcrExtractor  # type: ignore

    return OcrExtractor()


_DEFAULT_LOADERS: Dict[SourceType, Callable[[], Extractor]] = {
    SourceType.PDF: _load_pdf_extractor,
    SourceType.DOCX: _load_docx_extractor,
    SourceType.OCR: _load_ocr_extractor,
}

# 已注册实例（mock 注入或缓存）。
_INSTANCES: Dict[SourceType, Extractor] = {}


def register_extractor(source_type: SourceType, extractor: Extractor) -> None:
    """注册一个 extractor 实例（供测试注入 mock，或缓存真实 extractor）。"""
    _INSTANCES[source_type] = extractor


def unregister_extractor(source_type: SourceType) -> None:
    _INSTANCES.pop(source_type, None)


def _resolve_extractor(source_type: SourceType) -> Extractor:
    if source_type in _INSTANCES:
        return _INSTANCES[source_type]
    loader = _DEFAULT_LOADERS.get(source_type)
    if loader is None:
        # 已知但尚未实现的类型（html/xlsx/pptx）
        raise UnsupportedFormatError(
            f"暂不支持的 source_type={source_type.value}（未来支持）"
        )
    try:
        ext = loader()
    except ImportError as exc:
        extra = _EXTRA_FOR.get(source_type, "")
        hint = f"（pip install document2chunk[{extra}]）" if extra else ""
        raise MissingDependencyError(
            f"extractor 未就绪/依赖缺失：{exc.msg} {hint}".strip()
        ) from exc
    _INSTANCES[source_type] = ext
    return ext


# ---------------------------------------------------------------------------
# 源类型路由
# ---------------------------------------------------------------------------


def _coerce_source_type(value: Any) -> Optional[SourceType]:
    if value is None:
        return None
    if isinstance(value, SourceType):
        return value
    try:
        return SourceType(str(value))
    except ValueError:
        raise UnsupportedFormatError(f"非法 source_type: {value!r}")


def _source_name(source: Source) -> Optional[str]:
    if isinstance(source, (str, Path)):
        return Path(source).name
    return None


def _detect_by_name(source: Source) -> Optional[SourceType]:
    name = _source_name(source)
    if not name:
        return None
    ext = Path(name).suffix.lower()
    if ext == _PDF_EXT:
        return SourceType.PDF
    if ext == _DOCX_EXT:
        return SourceType.DOCX
    if ext in _IMAGE_EXTS:
        return SourceType.OCR
    return None


def _sniff_source_type(data: bytes) -> Optional[SourceType]:
    """按魔数嗅探（bytes 输入、无扩展名时）。"""
    if data[:5] == b"%PDF-":
        return SourceType.PDF
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:2] == b"BM":
        return SourceType.OCR
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return SourceType.OCR  # TIFF
    if data[:4] == b"GIF8":
        return SourceType.OCR
    if data[:2] == b"PK":  # ZIP 容器：docx（pptx/xlsx 未来）
        return SourceType.DOCX
    return None


# pdf editable/scanned 判定钩子（可被测试/真实 pdf_detect 覆盖）。
_PDF_KIND_DETECTOR: Optional[Callable[[Source], SourceType]] = None


def set_pdf_kind_detector(fn: Optional[Callable[[Source], SourceType]]) -> None:
    """注入 PDF editable/scanned 判定器（默认走 pipeline.pdf_detect，再退化为启发式）。"""
    global _PDF_KIND_DETECTOR
    _PDF_KIND_DETECTOR = fn


def _pdf_kind(source: Source) -> SourceType:
    """区分 editable PDF（→pdf-extractor）/ scanned·mixed（→ocr-extractor）。"""
    if _PDF_KIND_DETECTOR is not None:
        return _PDF_KIND_DETECTOR(source)
    try:
        from document2chunk.pipeline.pdf_detect import detect_pdf_type  # type: ignore
    except ImportError:
        detect_pdf_type = None
    if detect_pdf_type is not None:
        res = detect_pdf_type(source)  # DetectResult 或字符串
        kind = getattr(res, "pdf_type", res)  # 'editable' | 'scanned' | 'mixed'
        return SourceType.OCR if kind in ("scanned", "mixed") else SourceType.PDF
    return _pdf_kind_heuristic(source)


def _pdf_kind_heuristic(source: Source) -> SourceType:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.warning("无法判定 PDF editable/scanned（pdf_detect 与 PyMuPDF 均不可用），默认按 editable 路由")
        return SourceType.PDF
    if isinstance(source, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(source), filetype="pdf")
    else:
        doc = fitz.open(str(source))
    try:
        npages = max(len(doc), 1)
        sample = min(npages, 10)
        total = sum(len(doc[i].get_text().strip()) for i in range(sample))
        # ≥30 字/页 → editable（对齐 pdf_detect 页级启发式）
        return SourceType.PDF if total / sample >= 30 else SourceType.OCR
    finally:
        doc.close()


def _route_source_type(source: Source, explicit: Any) -> SourceType:
    st = _coerce_source_type(explicit)
    if st is not None:
        return st  # 显式优先

    by_name = _detect_by_name(source)
    if by_name == SourceType.PDF:
        return _pdf_kind(source)
    if by_name is not None:
        return by_name

    if isinstance(source, (bytes, bytearray)):
        sniffed = _sniff_source_type(bytes(source))
        if sniffed == SourceType.PDF:
            return _pdf_kind(source)
        if sniffed is not None:
            return sniffed

    name = _source_name(source)
    raise UnsupportedFormatError(
        f"不支持的源格式：{name or type(source).__name__}"
    )


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------


def _assemble(result: ExtractionResult, keep_toc: bool) -> LogicalDocument:
    try:
        from document2chunk.structure import assemble  # type: ignore
    except ImportError as exc:
        raise MissingDependencyError(f"structure-builder 未就绪：{exc.msg}") from exc
    return assemble(result, keep_toc=keep_toc)


# markdown 渲染钩子（默认走 export.to_markdown；缺失时返回 None）。
_MARKDOWN_FN: Optional[Callable[[LogicalDocument], str]] = None


def set_markdown_renderer(fn: Optional[Callable[[LogicalDocument], str]]) -> None:
    global _MARKDOWN_FN
    _MARKDOWN_FN = fn


def _to_markdown(doc: LogicalDocument) -> Optional[str]:
    if _MARKDOWN_FN is not None:
        return _MARKDOWN_FN(doc)
    try:
        from document2chunk.export import to_markdown  # type: ignore
    except ImportError:
        return None
    return to_markdown(doc)


def parse(
    source: Source,
    *,
    source_type: Any = None,
    keep_toc: bool = False,
    extract_images: bool = True,
    options: Optional[ParseOptions] = None,
) -> LogicalDocument:
    """统一解析入口：源路由 → extractor → structure.assemble → LogicalDocument。"""
    st = _route_source_type(source, source_type)
    extractor = _resolve_extractor(st)

    opts = options if options is not None else ParseOptions()
    opts.extract_images = extract_images

    result = extractor.extract(source, options=opts)
    doc = _assemble(result, keep_toc=keep_toc)

    name = _source_name(source)
    if doc.metadata.source_file is None and name:
        doc.metadata.source_file = name
    if doc.metadata.source_type is None:
        doc.metadata.source_type = st
    return doc


# ---------------------------------------------------------------------------
# FastAPI（可选依赖 [api]）
# ---------------------------------------------------------------------------


def create_app():
    """构建 FastAPI 应用（惰性导入，避免未安装 [api] extra 时拖累库导入）。"""
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="document2chunk", version=__version__)

    @app.exception_handler(UnsupportedFormatError)
    async def _unsupported(_, exc: UnsupportedFormatError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(MissingDependencyError)
    async def _missing(_, exc: MissingDependencyError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(Document2ChunkError)
    async def _d2c(_, exc: Document2ChunkError):
        # 覆盖 InvalidDocxError / InvalidPdfError 等子类 → 422
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.post("/parse")
    async def parse_file(
        request: Request,
        source_type: Optional[str] = None,
        keep_toc: bool = False,
        extract_images: bool = True,
    ):
        data, filename = await _read_upload(request)
        st = _coerce_source_type(source_type)
        doc = parse(
            data,
            source_type=st,
            keep_toc=keep_toc,
            extract_images=extract_images,
        )
        if filename and doc.metadata.source_file is None:
            doc.metadata.source_file = filename
        import json

        return {
            "document": json.loads(doc.model_dump_json(exclude_none=True)),
            "markdown": _to_markdown(doc),
        }

    return app


async def _read_upload(request) -> tuple[bytes, Optional[str]]:
    """从 multipart（file/document 字段）或原始请求体读取二进制。"""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        from fastapi import HTTPException

        try:
            form = await request.form()
        except Exception as exc:  # noqa: BLE001 - 通常因未装 python-multipart
            raise HTTPException(
                status_code=400,
                detail="multipart 解析失败：请安装 python-multipart（pip install document2chunk[api]）",
            ) from exc
        upload = form.get("file") or form.get("document")
        if upload is None:
            raise HTTPException(status_code=400, detail="multipart 请求缺少 file 字段")
        data = await upload.read()
        return data, getattr(upload, "filename", None)
    data = await request.body()
    return data, request.query_params.get("filename")


# ---------------------------------------------------------------------------
# CLI：python -m document2chunk.api
# ---------------------------------------------------------------------------


def main(argv: Optional[list] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m document2chunk.api", description="document2chunk HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
