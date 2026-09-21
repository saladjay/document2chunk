"""把本地 xlsx 目录批量投给 112 的 /parse-excel，结果 JSON 落到本地输出目录供人工检验。

用法：
    uv run python scripts/_parse_112_xlsx.py \
        --input "D:/document2chunk-test/xlsx" --output "D:/output" \
        [--api http://128.23.67.112:9301]

产出：
    <output>/<文件名>.json        每文件的完整行级 envelope（pretty UTF-8，可直接人工检验）
    <output>/<文件名>.error.txt   非 200 时的错误体
    <output>/_summary.md          汇总表（文件/HTTP/rows/blocks/warns/耗时）
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="D:/document2chunk-test/xlsx")
    ap.add_argument("--output", default="D:/output")
    ap.add_argument("--api", default="http://128.23.67.112:9301")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in in_dir.iterdir() if p.is_file())
    if not files:
        raise SystemExit(f"输入目录为空: {in_dir}")

    lines = ["| 文件 | HTTP | rows | blocks | warns | 耗时s | 产出 |", "|---|---|---|---|---|---|---|"]
    ok = fail = 0
    with httpx.Client(timeout=args.timeout) as client:
        health = client.get(f"{args.api}/health").json()
        print(f"health: {health}")
        for p in files:
            t0 = time.time()
            status = -1
            body = b""
            try:
                resp = client.post(
                    f"{args.api}/parse-excel",
                    files={"file": (p.name, p.read_bytes())},
                )
                status = resp.status_code
                body = resp.content
            except Exception as exc:  # 网络/超时
                body = f"请求异常: {type(exc).__name__}: {exc}".encode("utf-8")
            elapsed = time.time() - t0

            stem = p.stem
            product = "-"
            rows = blocks = warns = "-"
            if status == 200:
                env = json.loads(body)
                (out_dir / f"{stem}.json").write_text(
                    json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                rows = len(env.get("rows", []))
                blocks = len(env.get("blocks", []))
                warns = len(env.get("warnings", []))
                product = f"{stem}.json"
                ok += 1
            else:
                (out_dir / f"{stem}.error.txt").write_text(
                    f"HTTP {status}\n{body.decode('utf-8', errors='replace')}", encoding="utf-8"
                )
                product = f"{stem}.error.txt"
                fail += 1
            lines.append(f"| {p.name} | {status} | {rows} | {blocks} | {warns} | {elapsed:.1f} | {product} |")
            print(f"[{status}] {p.name}  rows={rows} warns={warns}  {elapsed:.1f}s")

    summary = out_dir / "_summary.md"
    summary.write_text(
        f"# 112 /parse-excel 批量处理汇总\n\n- 输入：`{in_dir}`（{len(files)} 个文件）\n"
        f"- API：`{args.api}/parse-excel`\n- 成功 {ok} / 失败 {fail}\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(f"\nsummary -> {summary}")


if __name__ == "__main__":
    main()
