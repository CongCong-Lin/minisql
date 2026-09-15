"""账号管理入口；密码从隐藏终端输入读取，不接受命令行密码参数。"""

import argparse
from getpass import getpass

from engine.session import connect
from engine.security import initialize, manage_user
from sql_compiler.errors import ExecuteError


def main(argv=None):
    parser = argparse.ArgumentParser(description="初始化认证或管理数据库账号")
    parser.add_argument("action", choices=["init", "create", "password", "drop"])
    parser.add_argument("name", help="目标用户名")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--user", help="已存在的管理员用户名")
    args = parser.parse_args(argv)
    session = None
    try:
        credential = getpass("管理员密码：") if args.action != "init" else None
        session = connect(args.data_dir, username=args.user, password=credential)
        password = getpass("目标账号的新密码：") if args.action != "drop" else None
        if password is not None and password != getpass("再次输入新密码："):
            raise ExecuteError("两次密码不一致")
        if args.action == "init":
            initialize(session, args.name, password)
        else:
            manage_user(session, args.action, args.name, password)
        print("账号管理操作成功")
        return 0
    except (ExecuteError, OSError) as exc:
        print(f"操作失败：{exc}")
        return 2
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
