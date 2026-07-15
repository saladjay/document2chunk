"""批量系统测试：遍历 PDF 文件夹，自动路由解析（edited/scanned/mixed），输出 markdown + json + 可选可视化 + 汇总 CSV。

用法：
    # 1) 先只分类（快，不解析）——看 edited/scanned/mixed 分布
    python scripts/batch_test.py D:/my_pdfs --classify-only

    # 2) 全量解析（edited 快；scanned/mixed 走远程 OCR，每页 ~9s）
    set DOCUMENT2CHUNK_OCR_TOKEN=xxxx          # Windows；Linux 用 export
    python scripts/batch_test.py D:/my_pdfs -o batch_out

    # 3) 抽样 + 可视化（慢，便于人工 QA）
    python scripts/batch_test.py D:/my_pdfs -o batch_out --sample 10 --viz

输出：batch_out/<pdf名>/ {output.md, document.json, viz/*.png}  +  batch_out/summary.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import traceback

# Windows GBK 控制台兼容：避免 ✓/中文 触发 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass
from collections import Counter
from pathlib import Path
from typing import Optional


def _classify(pdf: Path) -> str:
    """返回 editable / scanned / mixed（pdf_detect）。"""
    try:
        from document2chunk.pipeline.pdf_detect import detect_pdf_type

        res = detect_pdf_type(str(pdf))
        return getattr(res, "pdf_type", str(res))
    except Exception as e:
        return f"classify_error:{type(e).__name__}"


def _parse_one(pdf: Path, out_dir: Path, do_viz: bool) -> dict:
    """解析单个 PDF：分类 → 选 extractor（捕获中间结果）→ assemble → md/json/viz。"""
    from document2chunk.export import to_json, to_markdown
    from document2chunk.ir import SourceType
    from document2chunk.pipeline.pdf_detect import detect_pdf_type
    from document2chunk.structure import assemble

    row: dict = {"file": pdf.name, "group": pdf.parent.name, "status": "ok",
                 "pdf_type": "", "source_type": "", "pages": "", "blocks": "",
                 "headings": "", "tables": "", "images": "", "time_s": ""}
    t0 = time.time()

    # 分类
    try:
        kind = getattr(detect_pdf_type(str(pdf)), "pdf_type", "editable")
    except Exception:
        kind = "editable"
    row["pdf_type"] = kind

    sub = out_dir / pdf.parent.name / pdf.stem  # 保留组子目录结构
    sub.mkdir(parents=True, exist_ok=True)
    inter_dir = sub / "intermediate"

    if kind in ("scanned", "mixed"):
        from document2chunk.extractors.ocr import OcrExtractor

        result = OcrExtractor().extract(
            pdf, image_out_dir=str(sub / "images"), dump_dir=str(inter_dir)
        )
        st = SourceType.OCR
    else:
        from document2chunk.extractors.pdf import PdfExtractor

        result = PdfExtractor(debug_dir=str(inter_dir)).extract(pdf)
        st = SourceType.PDF

    doc = assemble(result)
    dt = time.time() - t0

    kinds = Counter(type(b).__name__ for b in doc.content)
    row.update({
        "source_type": st.value,
        "pages": doc.metadata.page_count or "",
        "blocks": len(doc.content),
        "headings": kinds.get("HeadingNode", 0),
        "tables": kinds.get("TableNode", 0),
        "images": kinds.get("ImageNode", 0),
        "time_s": round(dt, 1),
    })

    (sub / "output.md").write_text(to_markdown(doc), encoding="utf-8")
    (sub / "document.json").write_text(to_json(doc), encoding="utf-8")

    if do_viz:
        try:
            from document2chunk.debug import visualize

            visualize(doc, source_path=pdf, out_dir=sub / "viz", mode="overlay", dpi=150)
            row["viz"] = "yes"
        except Exception as e:
            row["viz"] = f"viz_error:{type(e).__name__}"
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description="批量 PDF 系统测试（自动路由 edited/scanned/mixed）")
    ap.add_argument("input_dir", help="PDF 所在文件夹")
    ap.add_argument("-o", "--out", default="batch_out", help="输出目录（默认 batch_out）")
    ap.add_argument("--ext", default=".pdf", help="文件扩展名（默认 .pdf）")
    ap.add_argument("--classify-only", action="store_true", help="只跑 pdf_detect 分类，不解析")
    ap.add_argument("--sample", type=int, default=0, help="只处理前 N 个（0=全部）")
    ap.add_argument("--viz", action="store_true", help="生成 bbox 叠加可视化（OCR 慢）")
    args = ap.parse_args()

    in_dir = Path(args.input_dir)
    pdfs = sorted(p for p in in_dir.rglob(f"*{args.ext}") if p.is_file())
    if args.sample:
        pdfs = pdfs[: args.sample]
    if not pdfs:
        print(f"未找到 *{args.ext}：{in_dir}")
        return

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"共 {len(pdfs)} 个文件 => {out_dir}")
    if args.classify_only:
        # 只分类
        rows = []
        for i, pdf in enumerate(pdfs, 1):
            kind = _classify(pdf)
            rows.append({"file": pdf.name, "pdf_type": kind})
            print(f"[{i}/{len(pdfs)}] {pdf.name}: {kind}")
        dist = Counter(r["pdf_type"] for r in rows)
        print("\n=== 分布 ===")
        for k, v in dist.most_common():
            print(f"  {k}: {v}")
        _write_csv(out_dir / "classify.csv", rows)
        print(f"\n分类表 → {out_dir/'classify.csv'}")
        return

    # 全量解析
    rows = []
    for i, pdf in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf.name} ...", flush=True)
        try:
            row = _parse_one(pdf, out_dir, args.viz)
        except Exception as e:
            row = {"file": pdf.name, "group": pdf.parent.name, "status": f"ERROR:{type(e).__name__}:{str(e)[:100]}",
                   "pdf_type": "", "source_type": "", "pages": "", "blocks": "",
                   "headings": "", "tables": "", "images": "", "time_s": ""}
            print("   [ERR]", row["status"])
            traceback.print_exc(limit=2)
        else:
            print(f"   [ok] {row['source_type']} | {row['pages']}p | {row['blocks']}块 "
                  f"(H{row['headings']}/T{row['tables']}/I{row['images']}) | {row['time_s']}s")
        rows.append(row)

    _write_csv(out_dir / "summary.csv", rows)
    print(f"\n=== 汇总 → {out_dir/'summary.csv'} ===")
    print(f"成功 {sum(1 for r in rows if r['status']=='ok')} / 失败 {sum(1 for r in rows if r['status']!='ok')}")
    dist = Counter(r["source_type"] for r in rows if r["status"] == "ok")
    print("source_type 分布:", dict(dist))


def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
