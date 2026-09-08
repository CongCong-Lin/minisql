"""A 负责：SQL 命令行入口存根。"""

import sys


def main(argv: list[str] | None = None) -> int:
    """明确报告尚未实现，避免把空运行当作验收成功。"""
    print("M1 骨架：SQL 命令行尚未实现，请运行 python -m pytest 验证接口。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
