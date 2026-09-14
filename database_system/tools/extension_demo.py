"""以独立命令行进程演示扩展，逐步核对预期并保留数据库及日志。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4


def collect_output(text):
    """提取公开 CLI 协议，忽略可选的编译过程展示行。"""
    queries, errors = [], []
    for line in text.splitlines():
        if line.startswith("COLUMNS: "):
            queries.append({"columns": json.loads(line[9:]), "rows": []})
        elif line.startswith("ROW: "):
            queries[-1]["rows"].append(json.loads(line[5:]))
        else:
            match = re.match(r"\[(LEXER|PARSER|SEMANTIC|PLANNER|EXECUTE)\] Error", line)
            if match:
                errors.append(match.group(1))
    return queries, errors


def main(argv=None):
    """运行独立数据库中的演示步骤，逐步校验并保存输出。"""
    parser = argparse.ArgumentParser(description="MiniSQL 扩展演示与结果校验")
    parser.add_argument("--data-dir", help="指定尚不存在的新目录；默认自动创建独立目录")
    parser.add_argument("--details", action="store_true", help="显示 Token、AST、原始及优化计划")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = Path(__file__).resolve().parents[1]
    examples = root / "examples" / "extensions"
    name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    data_dir = Path(args.data_dir).resolve() if args.data_dir else root / "data" / ("extensions-" + name)
    try:
        data_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"无法创建新的演示目录：{exc}", file=sys.stderr)
        return 2
    expected = json.loads((examples / "expected.json").read_text(encoding="utf-8"))
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    log_path = data_dir / "execution.log"
    print(f"演示数据库：{data_dir / 'minisql.db'}", flush=True)
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        for filename, wanted in expected.items():
            path = examples / filename
            title = f"\n执行 {filename}（每步重新启动数据库进程）\n"
            print(title + path.read_text(encoding="utf-8"), flush=True)
            command = [sys.executable, "-X", "utf8", "-m", "cli.main", str(path),
                       "--mode", "database", "--data-dir", str(data_dir)]
            if not args.details:
                command.extend(["--no-tokens", "--no-ast", "--no-plan", "--no-opt-plan"])
            completed = subprocess.run(command, cwd=root, env=env, text=True,
                                       encoding="utf-8", capture_output=True)
            print(completed.stdout, end="", flush=True)
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
            log.write(title + completed.stdout + completed.stderr + f"EXIT: {completed.returncode}\n")
            log.flush()
            queries, errors = collect_output(completed.stdout)
            if completed.returncode != wanted["exit"] or queries != wanted["queries"] or errors != wanted["errors"]:
                print(f"结果与预期不一致，演示停止。日志：{log_path}", file=sys.stderr)
                return 1
    print(f"\n全部演示结果已核对；错误示例按预期报错，后续查询和重启正常。\n日志：{log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
