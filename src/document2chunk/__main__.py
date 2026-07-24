"""python -m document2chunk 入口。

子命令：
- `cli`：解析文件 → result.md + images/（serve.cli_main）
- 无子命令 / `serve`：启动 HTTP 服务（api.main，/parse + /parse-pdf）

示例：
    python -m document2chunk cli --input a.pdf --output out/ --images out/images
    python -m document2chunk serve --host 0.0.0.0 --port 9300
    python -m document2chunk --host 0.0.0.0 --port 9300   # 默认启 HTTP 服务
"""
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "cli":
        from document2chunk.serve import cli_main
        return cli_main(argv[1:])
    # 默认：HTTP 服务（去掉可选的 "serve" 子命令）
    if argv and argv[0] == "serve":
        argv = argv[1:]
    from document2chunk.api import main as api_main
    return api_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
