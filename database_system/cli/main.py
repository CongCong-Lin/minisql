"""MiniSQL 命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from engine import runtime

# 公开模块级别别名，便于测试替身注入；实际调用仍转发到 runtime 生命周期接口。
def open_database(*args, **kwargs):
    return runtime.open_database(*args, **kwargs)


def run(*args, **kwargs):
    return runtime.run(*args, **kwargs)


def close_database(*args, **kwargs):
    return runtime.close_database(*args, **kwargs)


def _json(value: object) -> str:
    """按公共契约序列化 JSON 值。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cli.main")
    parser.add_argument("sql_file", nargs="?", help="SQL 文件路径；省略时从 stdin 读取")
    parser.add_argument("--mode", choices=("compiler", "database"), default="database")
    parser.add_argument("--data-dir", default=None, help="数据库目录，默认当前目录/data")
    # 同一 dest 的正、负开关按参数出现顺序处理，因此最后一次指定生效。
    for name, dest in (("tokens", "show_tokens"), ("ast", "show_ast"),
                       ("plan", "show_plan"), ("opt-plan", "show_opt_plan")):
        parser.add_argument(f"--{name}", dest=dest, action="store_true", default=True)
        parser.add_argument(f"--no-{name}", dest=dest, action="store_false")
    return parser


def _read_sql(path: str | None) -> str:
    """严格按 UTF-8 读取文件或标准输入，文本替身流保持原样。"""
    if path is None:
        # Windows 管道的默认编码可能是 GBK，不能用它解释 UTF-8 SQL。
        # 显式严格解码，避免环境中的 replace/surrogateescape 静默改写数据。
        reconfigure = getattr(sys.stdin, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8-sig")


def _configure_standard_streams() -> None:
    """固定命令行文本编码和换行，测试替身流则保持原样。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", newline="\n")


def _print_stmt(result, args: argparse.Namespace, *, out) -> None:
    """输出单条 StmtResult 已完成的阶段。"""
    if result.tokens is not None and args.show_tokens:
        print(f"TOKENS: {_json(result.tokens)}", file=out)
    if result.ast is not None and args.show_ast:
        print(f"AST: {_json(result.ast)}", file=out)
    if result.semantic_ok is True:
        print("SEMANTIC: OK", file=out)
    plans = result.plans or []
    if plans and args.show_plan:
        print(f"PLAN: {_json(plans[0])}", file=out)
    if len(plans) > 1 and args.show_opt_plan:
        print(f"OPT_PLAN: {_json(plans[1])}", file=out)

    exec_result = getattr(result, "exec_result", None)
    if args.mode == "database" and result.ok and exec_result is not None:
        # 查询（包括零行查询）展示表头与行；CREATE/INSERT/DELETE 仅展示消息。
        is_query = bool(exec_result.columns) or "selected" in exec_result.message
        if is_query:
            print(f"COLUMNS: {_json(exec_result.columns)}", file=out)
            for row in exec_result.rows:
                print(f"ROW: {_json(list(row))}", file=out)
        print(f"RESULT: {exec_result.message}", file=out)
    if not result.ok and result.error:
        print(str(result.error), file=out)


def main(argv: list[str] | None = None) -> int:
    """执行命令行请求并返回契约规定的退出码。"""
    _configure_standard_streams()
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    data_dir = Path(args.data_dir) if args.data_dir is not None else Path.cwd() / "data"
    data_dir = data_dir.expanduser().resolve()
    try:
        text = _read_sql(args.sql_file)
    except (OSError, UnicodeError) as exc:
        print(f"[IO] {exc}", file=sys.stderr)
        return 2

    catalog = storage = None
    sql_failed = False
    close_failed = False
    try:
        catalog, storage = open_database(str(data_dir), mode=args.mode)
        results = run(text, catalog, storage)
        for result in results:
            _print_stmt(result, args, out=sys.stdout)
            if not result.ok:
                sql_failed = True
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        # close_database 仍在 finally 中执行；先保留环境错误状态。
        return_code = 2
    else:
        return_code = None
    finally:
        if catalog is not None:
            try:
                close_database(catalog, storage)
            except Exception as exc:
                close_failed = True
                print(str(exc), file=sys.stderr)
    if close_failed:
        return 2
    if return_code is not None:
        return return_code
    return 1 if sql_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
