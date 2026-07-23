"""命令行入口。

用法：
    python -m mineru2doc <目录或 content_list.json> [-o out.md] [--demote]
    python -m mineru2doc <x.pdf> --base-url http://128.23.67.112:9030 [-o out.md]
"""

from __future__ import annotations

import argparse
from typing import List, Optional

from . import convert


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m mineru2doc",
        description="MinerU PDF 分析结果 → 多级标题 Markdown 文档（简化版）",
    )
    p.add_argument("input", help="输入：目录 / content_list.json / .pdf（需 --base-url）")
    p.add_argument("-o", "--output", default=None,
                   help="输出文件路径；'-' = stdout；缺省 = stdout")
    p.add_argument("--base-url", default=None,
                   help="MinerU 服务地址（.pdf 输入必填），如 http://128.23.67.112:9030")
    p.add_argument("--demote", action="store_true",
                   help="开启降误检（MinerU 误判的长句标题 → 正文），默认关")
    p.add_argument("--image-dir", default=None,
                   help="图片落盘目录；缺省随 -o 输出文件所在目录（保留 md 相对引用）")
    args = p.parse_args(argv)

    convert(
        args.input,
        base_url=args.base_url,
        demote=args.demote,
        output=args.output or "-",
        image_dir=args.image_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
