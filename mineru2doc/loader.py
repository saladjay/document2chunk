"""MinerU 结果加载（接缝）：FileLoader / HttpLoader。

两路输入形态不同，都归一到 :class:`MinerUDoc`（``List[Block]``）：

- :class:`FileLoader`：MinerU CLI 文件输出。
    - ``<name>_content_list.json``（富：text_level/bbox/page_idx）→ ``_items_to_blocks``。
    - 回退 ``*.md``（markdown）→ :func:`markdown_parser.parse_markdown`。
- :class:`HttpLoader`：``POST {base_url}/file_parse``（``backend=hybrid-engine``，
  ``return_images=true`` 当要落盘图片时）。MinerU v3.4.2 该端点**同步**返回
  ``results[<filename>].{md_content, images?}``（仅 markdown[+图片 base64]，无 content_list），
  故解析 md → Block。filename 键可能因 multipart 编码乱码，取首个 value。

**图片提取保存**（参考仓库 OCR ``_mapping._image_to_node``）：当传入 ``image_out_dir`` 时，
把图片字节落盘到 ``image_out_dir/<img_path>``（保留 md 里的相对引用 ``images/<hash>.jpg``，
使输出 markdown 就近解析）。HttpLoader 从响应 ``images`` dict（键=basename）取 base64 解码；
FileLoader 从源目录拷贝已存在的图片文件。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

from .markdown_parser import parse_markdown
from .model import (
    TEXT, TABLE, IMAGE, EQUATION, LIST_T,
    Block,
    MinerUDoc,
)


class MinerULoaderError(RuntimeError):
    """加载/解析 MinerU 结果失败。"""


# ──────────────────────────────────────────────────────────
#  content_list item → Block（FileLoader 富路径用）
# ──────────────────────────────────────────────────────────

def _to_bbox(v: Any) -> Optional[List[float]]:
    if isinstance(v, (list, tuple)) and len(v) >= 4:
        try:
            return [float(x) for x in v[:4]]
        except (TypeError, ValueError):
            return None
    return None


def _items_to_blocks(items: List[dict]) -> List[Block]:
    blocks: List[Block] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        kind = (it.get("type") or "").strip()
        page_idx = it.get("page_idx")
        bbox = _to_bbox(it.get("bbox"))

        if kind == "text":
            level = it.get("text_level")
            blocks.append(Block(
                type=TEXT,
                text=(it.get("text") or "").strip(),
                level=(int(level) if isinstance(level, (int, float)) and level >= 1 else None),
                page_idx=page_idx,
                bbox=bbox,
            ))
        elif kind == "image":
            blocks.append(Block(
                type=IMAGE,
                img_path=it.get("img_path") or it.get("image_path"),
                caption=it.get("img_caption") or it.get("caption"),
                page_idx=page_idx,
                bbox=bbox,
            ))
        elif kind == "table":
            blocks.append(Block(
                type=TABLE,
                table_body=(it.get("table_body") or it.get("html") or it.get("table_body_html")),
                caption=it.get("table_caption") or it.get("caption"),
                page_idx=page_idx,
                bbox=bbox,
            ))
        elif kind == "equation":
            blocks.append(Block(
                type=EQUATION,
                latex=(it.get("text") or it.get("latex")),
                page_idx=page_idx,
                bbox=bbox,
            ))
        elif kind == "list":
            raw_items = it.get("items") or []
            blocks.append(Block(
                type=LIST_T,
                items=[str(x).strip() for x in raw_items if str(x).strip()],
                page_idx=page_idx,
                bbox=bbox,
            ))
        else:
            blocks.append(Block(type=TEXT, text=(it.get("text") or "").strip(),
                                page_idx=page_idx, bbox=bbox))
    return blocks


# ──────────────────────────────────────────────────────────
#  图片落盘（参考 OCR _image_to_node）
# ──────────────────────────────────────────────────────────

def _save_http_images(blocks: List[Block], images: dict, image_out_dir: str) -> int:
    """响应 images dict（键=basename，值=base64）→ 落盘到 image_out_dir/<img_path>。"""
    n = 0
    for b in blocks:
        if b.type != IMAGE or not b.img_path:
            continue
        key = os.path.basename(b.img_path)  # dict 按 basename 索引
        b64 = images.get(key) or images.get(b.img_path)
        if not b64:
            continue
        dest = os.path.join(image_out_dir, b.img_path)
        os.makedirs(os.path.dirname(dest) or image_out_dir, exist_ok=True)
        try:
            with open(dest, "wb") as f:
                f.write(base64.b64decode(b64))
            n += 1
        except (OSError, ValueError):
            pass  # 落盘失败不阻断
    return n


def _copy_file_images(blocks: List[Block], source_root: Path, image_out_dir: str) -> int:
    """源目录已存在的图片文件 → 拷贝到 image_out_dir/<img_path>。"""
    n = 0
    for b in blocks:
        if b.type != IMAGE or not b.img_path:
            continue
        src = source_root / b.img_path
        if not src.exists():
            continue
        dest = os.path.join(image_out_dir, b.img_path)
        os.makedirs(os.path.dirname(dest) or image_out_dir, exist_ok=True)
        try:
            shutil.copy2(src, dest)
            n += 1
        except OSError:
            pass
    return n


# ──────────────────────────────────────────────────────────
#  FileLoader
# ──────────────────────────────────────────────────────────

def _find_source(path: Path) -> Tuple[str, Path, Path]:
    """返回 (kind, 文件路径, source_root)。kind ∈ {'content_list','md'}。

    source_root = 解析 img_path 相对路径的基准目录（content_list/md 所在目录）。
    """
    if path.is_file():
        suf = path.suffix.lower()
        if suf == ".json":
            return "content_list", path, path.parent
        if suf == ".md":
            return "md", path, path.parent
        raise MinerULoaderError(f"不支持的文件类型：{path}")
    if path.is_dir():
        cl = sorted(path.glob("*_content_list.json")) or sorted(path.glob("content_list.json"))
        if cl:
            return "content_list", cl[0], path
        mds = sorted(path.glob("*.md"))
        if mds:
            return "md", mds[0], path
        raise MinerULoaderError(f"目录下未找到 *_content_list.json 或 *.md：{path}")
    raise MinerULoaderError(f"输入既非文件也非目录：{path}")


class FileLoader:
    """从 MinerU 标准输出文件加载（content_list.json 富路径，或 *.md 回退）。"""

    def load(self, source: Union[str, os.PathLike], *, image_out_dir: Optional[str] = None) -> MinerUDoc:
        kind, path, source_root = _find_source(Path(source))
        if kind == "content_list":
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise MinerULoaderError(f"读取 content_list 失败：{path}：{e}") from e
            if not isinstance(items, list):
                raise MinerULoaderError(f"content_list 顶层应为数组：{path}")
            doc = MinerUDoc(blocks=_items_to_blocks(items))
        else:
            try:
                md = path.read_text(encoding="utf-8")
            except OSError as e:
                raise MinerULoaderError(f"读取 markdown 失败：{path}：{e}") from e
            doc = MinerUDoc(blocks=parse_markdown(md))
        if image_out_dir:
            _copy_file_images(doc.blocks, source_root, image_out_dir)
        return doc


# ──────────────────────────────────────────────────────────
#  HttpLoader（:9030/file_parse → md_content[ + images]）
# ──────────────────────────────────────────────────────────

def _extract_results(resp: Any) -> Tuple[str, Optional[dict]]:
    """从 /file_parse 响应取 (md_content, images|None)。"""
    if not isinstance(resp, dict):
        raise MinerULoaderError(f"/file_parse 响应非对象：{type(resp).__name__}")
    status = resp.get("status")
    results = resp.get("results")
    if not isinstance(results, dict) or not results:
        raise MinerULoaderError(
            f"/file_parse 未返回结果（status={status!r}）。任务可能排队中，稍后重试。"
        )
    first = next(iter(results.values()))
    if not isinstance(first, dict) or not first.get("md_content"):
        keys = list(first.keys()) if isinstance(first, dict) else []
        raise MinerULoaderError(
            "/file_parse results 内无 md_content（键：" + str(keys) + "）。请提供真实响应样本以适配。"
        )
    return first["md_content"], first.get("images")


class HttpLoader:
    """从 MinerU 服务（默认 :9030）实时解析 PDF。"""

    def __init__(self, base_url: str, *, timeout: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def load(self, source: Union[str, os.PathLike, bytes], *,
             image_out_dir: Optional[str] = None) -> MinerUDoc:
        if isinstance(source, (bytes, bytearray)):
            pdf_bytes, filename = bytes(source), "upload.pdf"
        else:
            pdf_bytes = Path(source).read_bytes()
            filename = Path(source).name
        resp = self._post_file_parse(pdf_bytes, filename, want_images=bool(image_out_dir))
        md, images = _extract_results(resp)
        doc = MinerUDoc(blocks=parse_markdown(md))
        if image_out_dir and images:
            _save_http_images(doc.blocks, images, image_out_dir)
        return doc

    def _post_file_parse(self, pdf_bytes: bytes, filename: str, *, want_images: bool) -> dict:
        try:
            import httpx  # 惰性导入：FileLoader 路径不需要
        except ImportError as e:  # pragma: no cover
            raise MinerULoaderError("HttpLoader 需要 httpx（pip install httpx）") from e

        url = self.base_url + "/file_parse"
        files = {"files": (filename, pdf_bytes, "application/pdf")}
        data = {"backend": "hybrid-engine"}
        if want_images:
            data["return_images"] = "true"  # 要落盘图片才请求，省带宽
        try:
            r = httpx.post(url, files=files, data=data, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise MinerULoaderError(f"调用 MinerU {url} 失败：{e}") from e
        if r.status_code != 200:
            raise MinerULoaderError(f"MinerU 返回 {r.status_code}：{r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise MinerULoaderError(f"MinerU 响应非 JSON：{e}") from e


# ──────────────────────────────────────────────────────────
#  统一入口
# ──────────────────────────────────────────────────────────

def load(source: Any, *, base_url: Optional[str] = None,
         image_out_dir: Optional[str] = None) -> MinerUDoc:
    """按输入类型分发：``.pdf`` + base_url → HttpLoader；目录/``.json``/``.md`` → FileLoader。

    传 ``image_out_dir`` 则把图片落盘到该目录（保留 md 相对引用）。
    """
    is_pdf = (
        (isinstance(source, (str, os.PathLike)) and Path(source).suffix.lower() == ".pdf")
        or isinstance(source, (bytes, bytearray))
    )
    if is_pdf:
        if not base_url:
            raise MinerULoaderError("PDF 输入需要 --base-url（MinerU 服务地址）")
        return HttpLoader(base_url).load(source, image_out_dir=image_out_dir)
    return FileLoader().load(source, image_out_dir=image_out_dir)
