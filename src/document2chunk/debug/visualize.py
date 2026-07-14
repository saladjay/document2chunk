"""可视化入口：IR 可视化 + 过程可视化 + 批量 + CLI。

- :func:`visualize` —— 消费 :class:`LogicalDocument`（+ 源文件），源感知地输出
  bbox 叠加图（PDF/OCR）或结构树（docx）。
- :func:`visualize_debug_dir` —— 消费 session ① 的 ``debug_dir``，每 stage×page
  一图 + 阶段对比图（复刻旧库）。
- CLI：``python -m document2chunk.debug.visualize <doc.json|debug_dir> [source] [opts]``
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Literal, Optional

from document2chunk.ir import LogicalDocument

from ._annotate import block_to_element, draw_annotations, draw_structure_tree
from ._comparison import (
    body_font_info_from,
    collect_page_indices,
    find_page,
    generate_stage_comparison,
    load_debug_jsons,
)
from ._render import (
    MissingPyMuPDFError,
    has_pymupdf,
    is_image,
    is_pdf,
    render_page_background,
    scale_for,
)

log = logging.getLogger(__name__)

VisualizeMode = Literal["overlay", "tree", "both"]


# ---------------------------------------------------------------------------
# IR 可视化
# ---------------------------------------------------------------------------


def _can_overlay(source_type, source_path) -> tuple[bool, str]:
    """是否可生成 bbox 叠加视图（需页面底图）。"""
    if source_path is None:
        return False, "未提供 source_path"
    source_path = Path(source_path)
    if not source_path.exists():
        return False, f"source_path 不存在: {source_path}"
    # docx 无页面底图（designs/001 D6）
    if str(source_type) == "docx":
        return False, "docx 无页面底图（仅结构树）"
    if is_image(source_path):
        return True, ""
    if is_pdf(source_path):
        if not has_pymupdf():
            return False, "未安装 PyMuPDF（pip install document2chunk[pdf]）"
        return True, ""
    return False, f"不支持的源格式: {source_path.suffix}"


def _body_from_metadata(doc: LogicalDocument) -> Optional[Dict[str, object]]:
    c = doc.metadata.custom or {}
    if c.get("body_font") or c.get("body_font_size"):
        return {"body_font": c.get("body_font"), "body_font_size": c.get("body_font_size")}
    return None


def _render_overlay(
    doc: LogicalDocument,
    source_path: str | Path,
    out: Path,
    dpi: int,
    pages: Optional[List[int]],
    source_type_str: str,
) -> List[Path]:
    source_path = Path(source_path)
    scale = scale_for(source_path, dpi)

    by_page: Dict[int, List[dict]] = defaultdict(list)
    for block in doc.iter_blocks():
        prov = block.provenance
        if not prov or prov.page_index is None or not prov.bbox:
            continue
        by_page[prov.page_index].append(block_to_element(block))

    if is_image(source_path):
        page_indices = [0]
    else:
        page_indices = sorted(by_page)

    target = pages if pages is not None else page_indices
    body = _body_from_metadata(doc)
    results: List[Path] = []
    for pi in target:
        elems = by_page.get(pi, [])
        try:
            img = render_page_background(source_path, pi, dpi)
        except (IndexError, MissingPyMuPDFError) as exc:
            log.warning("跳过 page %s: %s", pi, exc)
            continue
        annotated = draw_annotations(
            img,
            elems,
            scale=scale,
            header_text=f"{source_type_str} | Page {pi}",
            body_font_info=body,
        )
        fp = out / f"page_{pi:03d}_overlay.png"
        annotated.save(str(fp), "PNG")
        results.append(fp)
        log.info("叠加图 -> %s（%d 元素）", fp, len(elems))
    return results


def _source_label(doc: LogicalDocument, source_path) -> str:
    if source_path is not None:
        return Path(source_path).name
    return doc.metadata.source_file or doc.metadata.title or "document"


def visualize(
    doc: LogicalDocument,
    source_path: str | Path | None = None,
    out_dir: str | Path = "viz_out",
    *,
    dpi: int = 150,
    pages: Optional[List[int]] = None,
    mode: VisualizeMode = "both",
) -> List[Path]:
    """渲染 :class:`LogicalDocument` 为 PNG。

    源感知：PDF/OCR（有底图）默认叠加视图；docx 或无 source 默认结构树。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    st = doc.metadata.source_type
    source_type_str = st.value if st else "unknown"

    can_overlay, reason = _can_overlay(st, source_path)
    eff_mode: VisualizeMode = mode
    if mode in ("overlay", "both") and not can_overlay:
        log.warning("无法生成叠加视图（%s），降级为结构树视图", reason)
        eff_mode = "tree"

    results: List[Path] = []
    if eff_mode in ("overlay", "both"):
        results += _render_overlay(doc, source_path, out, dpi, pages, source_type_str)
    if eff_mode in ("tree", "both"):
        label = _source_label(doc, source_path)
        results.append(
            draw_structure_tree(doc, out_path=out / "structure_tree.png", header=f"Structure Tree — {label}")
        )
    return results


# ---------------------------------------------------------------------------
# 过程可视化（debug_dir）
# ---------------------------------------------------------------------------


def visualize_debug_dir(
    debug_dir: str | Path,
    source_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    dpi: int = 150,
    pages: Optional[List[int]] = None,
    no_comparison: bool = False,
) -> List[Path]:
    """消费 debug_dir：每 stage×page 一张叠加图 + 阶段对比图（复刻旧库）。"""
    debug_dir = Path(debug_dir)
    source_path = Path(source_path)
    out = Path(out_dir) if out_dir else debug_dir / "visualize" / source_path.name
    out.mkdir(parents=True, exist_ok=True)

    stages = load_debug_jsons(debug_dir)
    if not stages:
        log.warning("debug_dir 中未找到有效 stage JSON: %s", debug_dir)
        return []

    body = body_font_info_from(stages)
    target_pages = pages if pages is not None else collect_page_indices(stages)
    scale = scale_for(source_path, dpi)

    results: List[Path] = []
    for stage in stages:
        si = stage["stage_index"]
        sname = stage["stage_name"]
        stype = stage.get("stage_type", "?")
        for pi in target_pages:
            page = find_page(stage, pi)
            if page is None:
                continue
            try:
                img = render_page_background(source_path, pi, dpi)
            except (IndexError, MissingPyMuPDFError) as exc:
                log.warning("跳过 stage %s page %s: %s", si, pi, exc)
                continue
            annotated = draw_annotations(
                img,
                page.get("elements", []),
                scale=scale,
                header_text=f"Stage {si}: {sname} [{stype}] | Page {pi}",
                body_font_info=body,
            )
            stage_dir = out / f"stage{si:02d}_{sname}"
            stage_dir.mkdir(parents=True, exist_ok=True)
            fp = stage_dir / f"stage{si:02d}_{sname}_page{pi:03d}.png"
            annotated.save(str(fp), "PNG")
            results.append(fp)

    if not no_comparison:
        results += generate_stage_comparison(stages, target_pages, out)
    return results


def visualize_batch(sources: List[str | Path], **kwargs) -> None:
    """批量可视化。

    - ``*.json`` —— 作为 :class:`LogicalDocument` 走 :func:`visualize`。
    - 目录 —— 作为 debug_dir 走 :func:`visualize_debug_dir`（需在 kwargs 提供 ``source_path``）。
    """
    sources = [Path(s) for s in sources]
    for src in sources:
        if src.is_dir():
            sp = kwargs.pop("source_path", None)
            if sp is None:
                log.warning("跳过目录 %s：debug_dir 模式需 source_path", src)
                continue
            visualize_debug_dir(src, sp, **kwargs)
        elif src.suffix.lower() == ".json":
            doc = LogicalDocument.model_validate_json(src.read_text(encoding="utf-8"))
            visualize(doc, **kwargs)
        else:
            log.warning("跳过 %s：仅支持 .json 或目录", src)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_pages(s: Optional[str]) -> Optional[List[int]]:
    if not s:
        return None
    return [int(p.strip()) for p in s.split(",") if p.strip() != ""]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m document2chunk.debug.visualize",
        description="document2chunk 可视化：IR 结果或 debug_dir 过程态 → PNG",
    )
    parser.add_argument("target", help="LogicalDocument JSON 文件 或 debug_dir 目录")
    parser.add_argument("source", nargs="?", default=None, help="源文件路径（PDF/图片，叠加视图所需）")
    parser.add_argument("--dpi", type=int, default=150, help="渲染 DPI（默认 150）")
    parser.add_argument("--pages", default=None, help="逗号分隔页码（0-based），如 0,1,2")
    parser.add_argument("--mode", choices=["overlay", "tree", "both"], default="both", help="IR 模式视图（默认 both）")
    parser.add_argument("--out-dir", default=None, help="输出目录")
    parser.add_argument("--no-comparison", action="store_true", help="（debug_dir）不生成阶段对比图")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    target = Path(args.target)
    pages = _parse_pages(args.pages)

    if target.is_dir():
        if not args.source:
            parser.error("debug_dir 模式需要提供 source（PDF/图片路径）")
        paths = visualize_debug_dir(
            target,
            args.source,
            out_dir=args.out_dir,
            dpi=args.dpi,
            pages=pages,
            no_comparison=args.no_comparison,
        )
    elif target.suffix.lower() == ".json":
        doc = LogicalDocument.model_validate_json(target.read_text(encoding="utf-8"))
        paths = visualize(
            doc,
            source_path=args.source,
            out_dir=args.out_dir or "viz_out",
            dpi=args.dpi,
            pages=pages,
            mode=args.mode,
        )
    else:
        parser.error(f"不支持的目标: {target}（需 .json 文件或目录）")
        return 2

    print(f"完成：生成 {len(paths)} 个文件")
    for p in paths:
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
