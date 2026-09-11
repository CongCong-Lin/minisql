"""深层表达式的资源边界、完整流水线和多语句恢复回归。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from engine.runtime import close_database, open_database, run
from sql_compiler import compile_sql
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ParserError
from sql_compiler.lexer import tokenize
from sql_compiler.parser import MAX_EXPRESSION_DEPTH, MAX_EXPRESSION_NESTING, parse


ROOT = Path(__file__).resolve().parents[1]


def _parentheses(expression, count):
    return "(" * count + expression + ")" * count


def _balanced_condition(levels):
    """大量节点但浅层的条件不应被误当成长链拒绝。"""
    if levels == 0:
        return "id=1"
    child = _balanced_condition(levels - 1)
    return f"({child} AND {child})"


@pytest.fixture
def database(tmp_path):
    catalog, storage = open_database(str(tmp_path))
    setup = run("CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);", catalog, storage)
    assert all(result.ok for result in setup)
    try:
        yield catalog, storage
    finally:
        close_database(catalog, storage)


@pytest.mark.parametrize("expression", [
    _parentheses("id=1", MAX_EXPRESSION_NESTING),
    "NOT " * MAX_EXPRESSION_NESTING + "id=1",
    "NOT (" * (MAX_EXPRESSION_NESTING // 2) + "id=1" + ")" * (MAX_EXPRESSION_NESTING // 2),
    " AND ".join(["id=1"] * (MAX_EXPRESSION_DEPTH - 1)),
    " OR ".join(["id=1"] * (MAX_EXPRESSION_DEPTH - 1)),
    "+".join(["id"] * (MAX_EXPRESSION_DEPTH - 1)) + f"={MAX_EXPRESSION_DEPTH - 1}",
    _balanced_condition(8),
], ids=["括号边界", "否定边界", "混合嵌套边界", "与链边界", "或链边界", "算术链边界", "宽而浅的树"])
@pytest.mark.parametrize("extended", [False, True], ids=["基础查询", "扩展查询"])
def test_allowed_boundary_survives_compile_optimize_execute_and_json(database, expression, extended):
    suffix = " ORDER BY id" if extended else ""
    results = run(f"SELECT id FROM t WHERE {expression}{suffix};", *database)
    assert len(results) == 1 and results[0].ok, [result.error for result in results]
    assert results[0].exec_result.rows == [(1,)]
    assert results[0].exec_result.columns == ["id"]
    # CLI 会序列化 AST 及两份计划，合法边界必须能走完整条路径。
    json.dumps(results[0].ast)
    json.dumps(results[0].plans)


@pytest.mark.parametrize("expression,column,message", [
    (_parentheses("id=1", MAX_EXPRESSION_NESTING + 1),
     5 + MAX_EXPRESSION_NESTING, "嵌套不能超过"),
    ("NOT " * (MAX_EXPRESSION_NESTING + 1) + "id=1",
     5 + 4 * MAX_EXPRESSION_NESTING, "嵌套不能超过"),
    ("NOT (" * (MAX_EXPRESSION_NESTING // 2) + "(id=1)" + ")" * (MAX_EXPRESSION_NESTING // 2),
     5 + 5 * (MAX_EXPRESSION_NESTING // 2), "嵌套不能超过"),
    (" AND ".join(["id=1"] * MAX_EXPRESSION_DEPTH), 5, "树深度不能超过"),
])
def test_parser_rejects_one_over_boundary_at_original_location(expression, column, message):
    sql = "SELECT id FROM t\nWHERE\n    " + expression + ";"
    with pytest.raises(ParserError, match=message) as caught:
        parse(tokenize(sql))
    assert (caught.value.line, caught.value.column) == (3, column)


@pytest.mark.parametrize("sql", [
    "SELECT id FROM t WHERE " + _parentheses("id=1", 150) + ";",
    "SELECT id FROM t WHERE " + "(" * 150 + "id=1;",
    "SELECT id FROM t WHERE " + "NOT " * 500 + "id=1;",
    "SELECT id FROM t WHERE " + " AND ".join(["id=1"] * 500) + ";",
    "SELECT id FROM t WHERE " + " OR ".join(["id=1"] * 500) + ";",
    "DELETE FROM t WHERE " + " AND ".join(["id=1"] * 500) + ";",
    "UPDATE t SET id=2 WHERE " + _parentheses("id=1", 150) + ";",
    "UPDATE t SET id=" + "+".join(["id"] * 500) + ";",
    "SELECT a.id FROM t a JOIN t b ON " + " AND ".join(["a.id=b.id"] * 500) + ";",
    "SELECT COUNT(*) FROM t HAVING " + " AND ".join(["COUNT(*)>0"] * 500) + ";",
], ids=["深括号", "未闭合深括号", "长否定链", "长与链", "长或链", "删除条件", "更新条件",
        "更新赋值", "连接条件", "聚合条件"])
def test_over_limit_statement_recovers_without_writes(database, sql):
    results = run("SELECT id FROM t;\n" + sql +
                  "\nINSERT INTO t(id) VALUES(2); SELECT id FROM t ORDER BY id;", *database)
    assert [result.ok for result in results] == [True, False, True, True]
    failed = results[1]
    assert failed.error.startswith("[PARSER] Error at line 2, column ")
    assert "不能超过" in failed.error
    assert failed.ast is None and failed.plans == [] and failed.exec_result is None
    assert results[0].exec_result.rows == [(1,)]
    assert results[3].exec_result.rows == [(1,), (2,)]


def test_nesting_budget_is_per_path_and_resets_for_other_clauses(database):
    expression = _parentheses("id", MAX_EXPRESSION_NESTING)
    condition = _parentheses("id=1", MAX_EXPRESSION_NESTING)
    results = run(f"UPDATE t SET id={expression} WHERE {condition};"
                  f"SELECT id FROM t WHERE {condition} AND {condition};", *database)
    assert all(result.ok for result in results), [result.error for result in results]
    assert results[-1].exec_result.rows == [(1,)]


def test_compiler_mode_recovers_and_pure_compile_does_not_commit(tmp_path):
    catalog, storage = open_database(str(tmp_path), mode="compiler")
    bad = "SELECT id FROM t WHERE " + _parentheses("id=1", 150) + ";"
    try:
        results = run("CREATE TABLE t(id INT);" + bad + "CREATE TABLE later(id INT);", catalog)
        assert [result.ok for result in results] == [True, False, True]
        assert catalog.find_table("later") is not None
    finally:
        close_database(catalog, storage)
    untouched = Catalog()
    with pytest.raises(ParserError, match="嵌套不能超过"):
        compile_sql("CREATE TABLE t(id INT);" + bad, untouched)
    assert untouched.find_table("t") is None


@pytest.mark.parametrize("expression", [
    _parentheses("id=1", 150),
    " AND ".join(["id=1"] * 500),
], ids=["深括号", "长条件链"])
def test_cli_returns_sql_failure_and_prints_results_before_and_after(tmp_path, expression):
    sql = ("CREATE TABLE t(id INT);\nINSERT INTO t(id) VALUES(1);\n"
           f"SELECT id FROM t WHERE {expression};\n"
           "INSERT INTO t(id) VALUES(2); SELECT id FROM t ORDER BY id;")
    completed = subprocess.run(
        [sys.executable, "-m", "cli.main", "--data-dir", str(tmp_path)],
        cwd=ROOT, input=sql.encode("utf-8"), capture_output=True,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=30,
    )
    output = completed.stdout.decode("utf-8")
    assert completed.returncode == 1, completed.stderr.decode("utf-8") + output
    assert completed.stderr == b""
    assert "[PARSER] Error at line 3, column " in output
    assert "不能超过" in output and "recursion" not in output
    assert output.count("RESULT: 1 row inserted") == 2
    assert [json.loads(line[5:]) for line in output.splitlines()
            if line.startswith("ROW: ")] == [[1], [2]]
    assert "AST: " in output and "OPT_PLAN: " in output
    catalog, storage = open_database(str(tmp_path))
    try:
        assert run("SELECT id FROM t ORDER BY id;", catalog, storage)[0].exec_result.rows == [(1,), (2,)]
    finally:
        close_database(catalog, storage)
