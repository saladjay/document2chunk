"""mineru2doc 命令行。

子命令（每个对外 HTTP 接口都配一个客户端子命令）：

- ``convert <input>``：**本地管线**——MinerU 结果/content_list/md/pdf → Markdown
  （在本机跑 convert，直连 MinerU；不走部署服务）。
- ``parse <file>``：**客户端**——POST 文件到部署服务 ``/parse``，取回 zip，解出
  ``result.md``（→ -o）+ ``images/``。
- ``health``：**客户端**——GET 部署服务 ``/health``。

默认服务地址取环境变量 ``MINERU2DOC_URL``，否则 ``http://128.23.67.112:9300``（可用
``--service`` 覆盖）。客户端子命令惰性依赖 httpx。
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile
from typing import List, Optional

DEFAULT_SERVICE = os.environ.get("MINERU2DOC_URL", "http://128.23.67.112:9300")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m mineru2doc",
        description="mineru2doc：本地管线 + 部署服务客户端 CLI",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # convert（本地管线）
    pc = sub.add_parser("convert", help="本地管线：输入 → Markdown")
    pc.add_argument("input", help="目录 / content_list.json / .md / .pdf（.pdf 需 --base-url）")
    pc.add_argument("-o", "--output", default=None, help="输出文件；'-'=stdout；缺省=stdout")
    pc.add_argument("--base-url", default=None, help="MinerU 服务地址（本地 .pdf 解析用）")
    pc.add_argument("--demote", action="store_true", help="开启降误检")
    pc.add_argument("--image-dir", default=None, help="图片落盘目录（缺省随 -o）")

    # parse（客户端 → /parse）
    pp = sub.add_parser("parse", help="客户端：POST 文件到部署服务 /parse")
    pp.add_argument("file", help="待解析文件")
    pp.add_argument("-o", "--output", default="result.md", help="结果 Markdown 路径（默认 result.md）")
    pp.add_argument("--service", default=DEFAULT_SERVICE, help=f"服务地址（默认 {DEFAULT_SERVICE}）")
    pp.add_argument("--demote", action="store_true", help="开启降误检")

    # health（客户端 → /health）
    ph = sub.add_parser("health", help="客户端：GET 部署服务 /health")
    ph.add_argument("--service", default=DEFAULT_SERVICE, help=f"服务地址（默认 {DEFAULT_SERVICE}）")

    # cli（CLI 模式：被 Chai 调用，写 {outputDir}/result.md + images/，exit 0=成功）
    pcl = sub.add_parser("cli", help="CLI 模式：解析文件 → 写 outputDir/result.md + images/")
    pcl.add_argument("--input", required=True, help="原始文件绝对路径 {inputFile}")
    pcl.add_argument("--output", required=True, help="产物目录绝对路径 {outputDir}")
    pcl.add_argument("--images", default=None, help="图片目录绝对路径 {imageDir}（默认 {output}/images）")
    pcl.add_argument("--base-url", default=os.environ.get("MINERU_BASE_URL", "http://127.0.0.1:9030"),
                     help="MinerU 服务地址（CLI 模式本地解析用）")
    pcl.add_argument("--demote", action="store_true", help="开启降误检")

    args = p.parse_args(argv)
    return {
        "convert": _cmd_convert,
        "parse": _cmd_parse,
        "health": _cmd_health,
        "cli": _cmd_cli,
    }[args.cmd](args)


# ── convert（本地管线）──

def _cmd_convert(args) -> int:
    from . import convert

    convert(
        args.input,
        base_url=args.base_url,
        demote=args.demote,
        output=args.output or "-",
        image_dir=args.image_dir,
    )
    return 0


# ── parse（客户端 → /parse）──

def _cmd_parse(args) -> int:
    try:
        import httpx
    except ImportError as e:  # pragma: no cover
        print("parse 子命令需要 httpx（pip install httpx）", file=sys.stderr)
        return 2

    url = args.service.rstrip("/") + "/parse"
    try:
        with open(args.file, "rb") as f:
            files = {"file": (os.path.basename(args.file), f.read())}
        data = {"demote": "true"} if args.demote else None
        r = httpx.post(url, files=files, data=data, timeout=600.0)
    except httpx.HTTPError as e:
        print(f"请求失败：{e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"读取文件失败：{e}", file=sys.stderr)
        return 1

    if r.status_code != 200:
        print(f"服务返回 HTTP {r.status_code}：{r.text[:300]}", file=sys.stderr)
        return 1

    out_dir = os.path.dirname(os.path.abspath(args.output)) or "."
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        saved = _safe_extract(z, out_dir)
    except (zipfile.BadZipFile, ValueError) as e:
        print(f"响应非合法 zip：{e}", file=sys.stderr)
        return 1

    # result.md → args.output（默认同名则不重命名）
    extracted_md = os.path.join(out_dir, "result.md")
    target_md = os.path.abspath(args.output)
    if os.path.exists(extracted_md) and os.path.abspath(extracted_md) != target_md:
        os.replace(extracted_md, target_md)
    n_img = sum(1 for n in saved if n.startswith("images/"))
    print(f"已保存 {args.output}（{n_img} 张图片在 {out_dir}/images/）")
    return 0


def _safe_extract(z: zipfile.ZipFile, dest_dir: str) -> List[str]:
    """解压 zip 到 dest_dir，带 zip-slip 防护；返回已解压条目名。"""
    dest_dir = os.path.abspath(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    saved: List[str] = []
    for info in z.infolist():
        target = os.path.abspath(os.path.join(dest_dir, info.filename))
        if os.path.commonpath([dest_dir, target]) != dest_dir:
            raise ValueError(f"zip-slip 拒绝：{info.filename}")
        z.extract(info, dest_dir)
        saved.append(info.filename)
    return saved


# ── health（客户端 → /health）──

def _cmd_health(args) -> int:
    try:
        import httpx
    except ImportError as e:  # pragma: no cover
        print("health 子命令需要 httpx（pip install httpx）", file=sys.stderr)
        return 2

    url = args.service.rstrip("/") + "/health"
    try:
        r = httpx.get(url, timeout=10.0)
    except httpx.HTTPError as e:
        print(f"请求失败：{e}", file=sys.stderr)
        return 1
    print(f"HTTP {r.status_code}：{r.text}")
    return 0 if r.status_code == 200 else 1


# ── cli（CLI 模式：被 Chai 调用，写 result.md + images/，exit 0=成功）──

def _cmd_cli(args) -> int:
    from . import convert_to_dir

    try:
        result = convert_to_dir(args.input, args.output, args.images,
                                base_url=args.base_url, demote=args.demote)
        print(f"已写入 {result}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"解析失败：{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
