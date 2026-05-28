#!/usr/bin/env python3
"""统一入口：默认执行检测；支持 web/serve 子命令启动服务。"""
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def main(argv: list[str] | None = None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"web", "serve"}:
        from web import main as web_main

        return web_main(args[1:])

    from cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    main()
