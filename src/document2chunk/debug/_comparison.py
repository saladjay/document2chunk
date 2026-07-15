"""debug_dir 加载与阶段对比图（过程模式）。

消费 session ① 写入的 ``{NN}_{name}.json``（schema 见 INTEGRATION §4），
复刻 ``doc-paddle-ocr/visualize_pipeline.py`` 的 ``generate_stage_comparison``。
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw

from ._style import STATS_FONT_SIZE, HEADER_FONT_SIZE, color_for, load_font

log = logging.getLogger(__name__)


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_debug_jsons(debug_dir: str | Path) -> List[Dict[str, Any]]:
    """加载 debug 目录下所有 stage JSON，按 ``stage_index`` 排序。

    对 ``body_analysis`` stage 推断正文基准字体/字号（挂 ``_body_font``/
    ``_body_font_size``），供统计面板与对比图使用。
    """
    debug_dir = Path(debug_dir)
    stages: List[Dict[str, Any]] = []
    for f in sorted(debug_dir.glob("*.json")):
        try:
            data = load_json(f)
        except Exception as exc:  # noqa: BLE001
            log.warning("跳过 %s: %s", f.name, exc)
            continue
        if "stage_index" in data and "pages" in data:
            stages.append(data)

    stages.sort(key=lambda s: s.get("stage_index", 0))

    for s in stages:
        if s.get("stage_name") == "body_analysis":
            font, size = _infer_body_font(s)
            s["_body_font"] = font
            s["_body_font_size"] = size
            break
    return stages


def body_font_info_from(stages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """从已加载的 stages 中取正文基准（body_analysis stage）。"""
    for s in stages:
        if s.get("_body_font"):
            return {"body_font": s["_body_font"], "body_font_size": s["_body_font_size"]}
    return None


def _infer_body_font(stage_data: Dict[str, Any]):
    """从 body_analysis stage 的 spans 统计正文 (font, size)。"""
    style_counts: Dict[tuple, int] = defaultdict(int)
    for page in stage_data.get("pages", []):
        for elem in page.get("elements", []):
            for span in elem.get("spans", []):
                text = span.get("text", "").strip()
                if text:
                    key = (span.get("font", ""), round(span.get("size", 0), 1))
                    style_counts[key] += len(text)
    if style_counts:
        return max(style_counts.items(), key=lambda x: x[1])[0]
    return ("Unknown", 12.0)


def find_page(stage_data: Dict[str, Any], page_index: int) -> Optional[Dict[str, Any]]:
    for page in stage_data.get("pages", []):
        if page.get("page_index") == page_index:
            return page
    return None


def collect_page_indices(stages: List[Dict[str, Any]]) -> List[int]:
    indices = set()
    for s in stages:
        for page in s.get("pages", []):
            indices.add(page.get("page_index", 0))
    return sorted(indices)


# 固定类型顺序（IR 优先，过程态次之），用于对比图配色与排序。
_TYPE_ORDER = [
    "paragraph", "heading", "title", "table", "list", "image",
    "formula", "toc", "toc_entry", "toc_title", "page_number", None,
]


def generate_stage_comparison(
    all_stages: List[Dict[str, Any]],
    target_pages: List[int],
    output_dir: str | Path,
) -> List[Path]:
    """每页一张条形图：展示各 stage 的类型分布变化（复刻旧库）。"""
    font = load_font(STATS_FONT_SIZE)
    font_header = load_font(HEADER_FONT_SIZE)

    comparison_dir = Path(output_dir) / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)

    all_types = set()
    for stage in all_stages:
        for page in stage.get("pages", []):
            for elem in page.get("elements", []):
                all_types.add(elem.get("type"))
    sorted_types = [t for t in _TYPE_ORDER if t in all_types]

    results: List[Path] = []
    for page_idx in target_pages:
        matrix: List[Counter] = []
        stage_labels: List[str] = []
        for stage in all_stages:
            page_data = find_page(stage, page_idx)
            if page_data is None:
                continue
            matrix.append(Counter(elem.get("type") for elem in page_data.get("elements", [])))
            stage_labels.append(f"S{stage['stage_index']}:{stage['stage_name']}")

        if not matrix:
            continue

        n_stages = len(matrix)
        bar_h = 16
        row_h = bar_h + 6
        left_margin = 180
        top_margin = 50
        right_margin = 30
        bar_max_w = 300
        max_count = max(
            (counts.get(t, 0) for counts in matrix for t in sorted_types),
            default=1,
        )
        max_count = max(max_count, 1)

        img_w = left_margin + bar_max_w + right_margin + 80
        img_h = top_margin + n_stages * row_h + 40
        img = Image.new("RGB", (img_w, img_h), (255, 255, 255))
        draw = ImageDraw.Draw(img)

        draw.text((10, 8), f"Stage Comparison - Page {page_idx}", fill=(30, 30, 30), font=font_header)

        for i, (counts, label) in enumerate(zip(matrix, stage_labels)):
            y = top_margin + i * row_h
            draw.text((5, y), label, fill=(50, 50, 50), font=font)
            bar_x = left_margin
            for t in sorted_types:
                cnt = counts.get(t, 0)
                if cnt == 0:
                    continue
                color = color_for(t)
                bar_w = max(int(cnt / max_count * bar_max_w), 2)
                draw.rectangle([bar_x, y, bar_x + bar_w, y + bar_h], fill=color)
                draw.text((bar_x + bar_w + 3, y), str(cnt), fill=color, font=font)
                bar_x += bar_w + 25

        legend_y = top_margin + n_stages * row_h + 10
        legend_x = 10
        for t in sorted_types:
            color = color_for(t)
            label = t or "null"
            draw.rectangle([legend_x, legend_y, legend_x + 10, legend_y + 10], fill=color)
            draw.text((legend_x + 13, legend_y - 1), label, fill=(60, 60, 60), font=font)
            legend_x += font.getbbox(label)[2] + 28

        filepath = comparison_dir / f"comparison_page{page_idx:03d}.png"
        img.save(str(filepath), "PNG")
        log.info("对比图 -> %s", filepath)
        results.append(filepath)

    return results
