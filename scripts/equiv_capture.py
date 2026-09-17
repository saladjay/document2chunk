"""equiv_capture —— 提效前后解析结果对照·采集端。

用法:
  python scripts/equiv_capture.py --list                          # 打印确定性选样清单(驱动循环用)
  python scripts/equiv_capture.py --single <文件路径> --root <根>  # 单文件采集(每文件独立子进程)
  python scripts/equiv_capture.py --watchdog 900                  # 单文件看门狗秒数(默认 900)

产物布局(<root>/<序号>_<净化名>/{out/result.md, img/, manifest 记录}):
  <root>/manifest.jsonl   每文件一行 JSON:路径、大小、result.md 的 md5、
                          有序图片 (name, md5) 表、块/章节数、分型计数、耗时。
                          逐行追加,单文件崩溃不丢前面进度。

选样规则(确定性,前后两次运行必须选到同一批文件):
  - docx(D:\\document2chunk-test\\docx):按大小降序取前 5;其余按大小升序均匀取 15。
  - pdf(D:\\document2chunk-test\\data):全部登记;editable 的取"最大 3 + 按名前 7",
    scanned/mixed 只登记 SKIPPED_OCR(远程 OCR 响应非确定性,不做 hash 对照)。
  - 其余扩展名(.doc/.wps 等走 soffice 的)不采集——本机无 LibreOffice。

参见 docs/大文件提效调研.md §7(效果等价性审查)。
"""
from __future__ import annotations

import argparse
import collections
import faulthandler
import hashlib
import json
import re
import shutil
import sys
import time
from pathlib import Path

DOCX_DIR = Path(r"D:\document2chunk-test\docx")
PDF_DIR = Path(r"D:\document2chunk-test\data")
TOP_DOCX = 5
SPREAD_DOCX = 15
TOP_PDF = 3
FIRST_PDF = 7
HASH_CHUNK = 1 << 20


def _md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name)[:60]


def select_corpus() -> list[Path]:
    """确定性选样。返回绝对路径列表(顺序即执行顺序)。"""
    docx = sorted(DOCX_DIR.glob("*.docx"), key=lambda p: p.stat().st_size, reverse=True)
    picked = list(docx[:TOP_DOCX])
    rest = sorted(docx[TOP_DOCX:], key=lambda p: p.stat().st_size)  # 升序均匀铺
    if rest:
        step = max(1, len(rest) // SPREAD_DOCX)
        picked.extend(rest[::step][:SPREAD_DOCX])

    pdfs = sorted(PDF_DIR.glob("*.pdf"), key=lambda p: p.stat().st_size, reverse=True)
    picked.extend(pdfs[:TOP_PDF] + sorted(PDF_DIR.glob("*.pdf"))[:FIRST_PDF])

    seen: set[str] = set()
    out = []
    for p in picked:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def _pdf_kind_of(path: Path) -> str:
    try:
        from document2chunk.pipeline.pdf_detect import detect_pdf_type
        res = detect_pdf_type(str(path))
        return str(getattr(res, "pdf_type", res))
    except Exception as e:  # noqa: BLE001
        return f"DETECT_ERROR:{type(e).__name__}"


def capture_single(path: Path, root: Path) -> dict:
    from document2chunk import serve

    idx = int(time.time() * 1000) % 100000  # 目录唯一性够用;对照按 file 字段匹配
    case = root / f"{idx:05d}_{_safe_name(path.name)}"
    img = case / "img"
    img.mkdir(parents=True, exist_ok=True)

    entry: dict = {"file": str(path), "size_bytes": path.stat().st_size}
    if path.suffix.lower() == ".pdf":
        kind = _pdf_kind_of(path)
        entry["pdf_type"] = kind
        if kind != "editable":
            entry["status"] = "SKIPPED_OCR"
            entry["reason"] = f"pdf_type={kind},OCR 路不做 hash 对照"
            return entry

    t0 = time.perf_counter()
    doc = serve.parse_to_files(str(path), str(case / "out"), str(img))
    entry["elapsed_s"] = round(time.perf_counter() - t0, 2)
    entry["md5_result_md"] = _md5(case / "out" / "result.md")

    images = []
    for p in sorted(img.rglob("*")):
        if p.is_file():
            images.append([p.relative_to(img).as_posix(), _md5(p)])
    entry["images"] = images

    content = list(doc.content)
    entry["n_blocks"] = len(content)
    entry["n_sections"] = sum(1 for _ in doc.iter_sections())
    entry["type_counts"] = dict(collections.Counter(type(b).__name__ for b in content))
    entry["source_type"] = str(doc.metadata.source_type)
    entry["status"] = "OK"
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--single", type=str)
    ap.add_argument("--drive", action="store_true", help="遍历选样,逐件子进程执行(隔离毒件)")
    ap.add_argument("--root", type=str, default=r"D:\document2chunk-equiv\run")
    ap.add_argument("--watchdog", type=int, default=900)
    args = ap.parse_args()

    if args.list:
        for p in select_corpus():
            print(p)
        return 0

    if args.drive:
        import subprocess
        me = [sys.executable, str(Path(__file__).resolve()), "--root", args.root,
              "--watchdog", str(args.watchdog)]
        n_bad = 0
        for p in select_corpus():
            r = subprocess.run(me + ["--single", str(p)], encoding="utf-8", errors="replace")
            if r.returncode != 0:
                n_bad += 1
        print(f"[drive] 完成,非零退出 {n_bad} 件")
        return 0

    if not args.single:
        ap.error("需要 --single <文件> 或 --list")
        return 2

    path = Path(args.single)
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)

    faulthandler.dump_traceback_later(args.watchdog, exit=True)
    t0 = time.perf_counter()
    try:
        entry = capture_single(path, root)
        entry["watchdog_hit"] = False
    except BaseException as e:  # noqa: BLE001  看门狗exit=True 不会到这;这里收业务异常
        entry = {
            "file": str(path),
            "size_bytes": path.stat().st_size if path.exists() else None,
            "status": f"ERROR:{type(e).__name__}",
            "reason": str(e)[:300],
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "watchdog_hit": False,
        }
    finally:
        faulthandler.cancel_dump_traceback_later()

    with open(root / "manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[capture] {entry['status']} {path.name} "
          f"{entry.get('elapsed_s', '-')}s md5={entry.get('md5_result_md', '-')}")
    return 0 if entry["status"] in ("OK", "SKIPPED_OCR") else 1


if __name__ == "__main__":
    sys.exit(main())
