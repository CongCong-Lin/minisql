"""A 负责：命令行与用例执行器端到端测试。

真实数据库链路由 D/C/B 提供；这里先用替身验证 CLI 协议和生命周期。
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

from utils.results import ExecuteResult, StmtResult


def _result(*, ok=True, error=None, mode="query"):
    if mode == "query":
        execution = ExecuteResult([(1, "Alice")], "1 row selected", ["id", "name"])
    else:
        execution = ExecuteResult([], "OK", [])
    return StmtResult(ok, error, [{"type": "EOF", "lexeme": "", "line": 1, "column": 1}],
                      [{"node": "FakeStmt"}], [{"op": "Fake"}, {"op": "FakeOpt"}],
                      execution if ok else None, True)


def test_cli_prints_fixed_sections_and_calls_runtime_in_order(monkeypatch):
    import cli.main as cli

    calls = []

    def fake_open(path, *, mode):
        calls.append(("open", path, mode))
        return object(), object()

    def fake_run(text, catalog, storage):
        calls.append(("run", text, catalog, storage))
        return [_result()]

    def fake_close(catalog, storage):
        calls.append(("close", catalog, storage))

    monkeypatch.setattr(cli.runtime, "open_database", fake_open)
    monkeypatch.setattr(cli.runtime, "run", fake_run)
    monkeypatch.setattr(cli.runtime, "close_database", fake_close)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("SELECT 1 FROM t;"))

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main(["--mode", "database", "--data-dir", "data"])

    assert code == 0
    assert [item[0] for item in calls] == ["open", "run", "close"]
    output = stdout.getvalue()
    assert output.index("TOKENS:") < output.index("AST:") < output.index("PLAN:")
    assert output.index("PLAN:") < output.index("OPT_PLAN:") < output.index("COLUMNS:")
    assert "ROW: [1,\"Alice\"]" in output
    assert output.endswith("RESULT: 1 row selected\n")
    assert stderr.getvalue() == ""


def test_cli_sql_error_returns_one_and_still_closes(monkeypatch):
    import cli.main as cli

    closed = []
    monkeypatch.setattr(cli.runtime, "open_database", lambda path, *, mode: (object(), None))
    monkeypatch.setattr(cli.runtime, "run", lambda text, catalog, storage: [_result(
        ok=False, error="[PARSER] Error at line 1, column 1: bad")])
    monkeypatch.setattr(cli.runtime, "close_database", lambda catalog, storage: closed.append(True))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("bad;"))

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main([])
    assert code == 1
    assert closed == [True]
    assert "[PARSER] Error at line 1, column 1: bad" in stdout.getvalue()
    assert stderr.getvalue() == ""
