"""启动 MiniSQL Studio 桌面窗口。

在 database_system 目录运行：

    python -m tools.studio
"""

from studio.app import main

if __name__ == "__main__":
    raise SystemExit(main())
