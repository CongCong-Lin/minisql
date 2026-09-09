"""MiniSQL SQL 基准用例执行器。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.case_runner")
    parser.add_argument("--suite", choices=("compiler", "execution", "all"),
                        default="all")
    return parser


def _load_expected(path: Path) -> tuple[str, int]:
    """读取 expected 文件并解释末尾 EXIT 行。"""
    text = path.read_text(encoding="utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    index = len(lines) - 1
    while index >= 0 and not lines[index].strip():
        index -= 1
    if index < 0:
        raise ValueError("expected 文件最后一个非空行必须是 EXIT: N")
    import re
    matched = re.fullmatch(r"EXIT:\s*(-?\d+)", lines[index])
    if matched is None:
        raise ValueError("expected 文件最后一个非空行必须是 EXIT: N")
    code = int(matched.group(1))
    # EXIT 行之前的内容构成完整 stdout 基准；忽略文件末尾多余换行。
    expected = "\n".join(lines[:index]).rstrip("\n")
    return expected, code


def _run_case(case_path: Path, suite: str) -> tuple[bool, str | None, bool]:
    """在独立临时目录运行一个 SQL 用例。"""
    expected_path = case_path.with_suffix(".expected")
    if not expected_path.exists():
        return False, f"{case_path.name}: 缺少同名 expected 文件", True
    try:
        expected, expected_code = _load_expected(expected_path)
        sql = case_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        return False, f"{case_path.name}: {exc}", True

    mode = "compiler" if suite == "compiler" else "database"
    runner_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        with tempfile.TemporaryDirectory(prefix="minisql-case-") as data_dir:
            completed = subprocess.run(
                [sys.executable, "-m", "cli.main", "--mode", mode,
                 "--data-dir", data_dir],
                cwd=str(runner_root), input=sql, text=True,
                encoding="utf-8", capture_output=True, env=environment,
            )
    except OSError as exc:
        return False, f"{case_path.name}: 无法启动 CLI：{exc}", True

    actual = completed.stdout.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    if completed.returncode != expected_code or actual != expected:
        detail = [
            f"{suite}/{case_path.stem}: FAIL",
            f"expected exit {expected_code}, actual {completed.returncode}",
        ]
        if actual != expected:
            detail.extend(("--- expected stdout ---", expected,
                           "--- actual stdout ---", actual))
        if completed.stderr:
            detail.extend(("--- stderr ---", completed.stderr.rstrip("\n")))
        return False, "\n".join(detail), False
    return True, None, False


def main(argv: list[str] | None = None) -> int:
    """运行指定套件并按用例结果返回 0/1，runner 故障返回 2。"""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    root = Path(__file__).resolve().parents[1] / "tests" / "cases"
    suites = ("compiler", "execution") if args.suite == "all" else (args.suite,)
    failures = 0
    runner_errors = 0
    for suite in suites:
        suite_dir = root / suite
        if not suite_dir.exists():
            print(f"套件目录不存在: {suite_dir}", file=sys.stderr)
            runner_errors += 1
            continue
        cases = sorted(suite_dir.glob("*.sql"), key=lambda p: p.name)
        if not cases:
            print(f"套件中没有 SQL 用例: {suite_dir}", file=sys.stderr)
            runner_errors += 1
            continue
        for case in cases:
            ok, message, runner_error = _run_case(case, suite)
            if ok:
                print(f"{suite}/{case.stem}: PASS")
            elif runner_error:
                print(message, file=sys.stderr)
                runner_errors += 1
            else:
                print(message or f"{suite}/{case.stem}: FAIL", file=sys.stderr)
                failures += 1
    if runner_errors:
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
