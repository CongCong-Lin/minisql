"""A/B/C/D 贯通联调：真实 Lexer/Parser/语义/计划/执行/生命周期，不用模块替身。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.runtime import close_database, open_database, run
from sql_compiler import compile_sql
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ParserError, SemanticError

ROOT = Path(__file__).resolve().parents[1]
CRUD = """
CREATE TABLE student(id INT, name VARCHAR);
INSERT INTO student(id, name) VALUES(1, 'Ada');
INSERT INTO student(name, id) VALUES('Bob', 2);
SELECT * FROM student;
SELECT name FROM student WHERE id = 2;
DELETE FROM student WHERE id = 1;
SELECT * FROM student;
"""


def _cli(data_dir: Path, sql: str, mode: str = "database", extra: list[str] | None = None,
         sql_file: Path | None = None) -> subprocess.CompletedProcess:
    """用真实命令行走完整生命周期。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    command = [sys.executable, "-B", "-m", "cli.main", "--mode", mode,
               "--data-dir", str(data_dir)]
    if extra:
        command.extend(extra)
    kwargs = dict(cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", env=env)
    if sql_file is not None:
        command.append(str(sql_file))
        return subprocess.run(command, **kwargs)
    return subprocess.run(command, input=sql, **kwargs)


def _roundtrip(tmp_path: Path, sql: str, mode: str = "database"):
    catalog, storage = open_database(str(tmp_path), mode=mode)
    try:
        return run(sql, catalog, storage), catalog, storage
    finally:
        close_database(catalog, storage)


def test_database_crud_insert_reorder_where_and_delete(tmp_path):
    """建表、列重排插入、WHERE 查询和删除必须走真实存储。"""
    results, catalog, _storage = _roundtrip(tmp_path, CRUD)
    assert all(item.ok for item in results)
    assert results[0].exec_result.message == "OK"
    assert results[1].exec_result.message == "1 row inserted"
    assert results[3].exec_result.rows == [(1, "Ada"), (2, "Bob")]
    assert results[3].exec_result.columns == ["id", "name"]
    assert results[4].exec_result.rows == [("Bob",)]
    assert results[4].exec_result.columns == ["name"]
    assert results[5].exec_result.message == "1 row deleted"
    assert results[6].exec_result.rows == [(2, "Bob")]
    assert catalog.find_table("STUDENT") is not None


def test_database_survives_process_restart(tmp_path):
    """关闭后换新进程，表结构和记录都要还在。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUTF8"] = "1"
    writer = """
from engine.runtime import close_database, open_database, run
catalog, storage = open_database(%r, mode='database')
run("CREATE TABLE student(id INT, name VARCHAR); INSERT INTO student(id, name) VALUES(7, 'Ada');",
    catalog, storage)
close_database(catalog, storage)
""" % str(tmp_path)
    reader = """
from engine.runtime import close_database, open_database, run
catalog, storage = open_database(%r, mode='database')
results = run("SELECT * FROM student;", catalog, storage)
assert results[0].ok
assert results[0].exec_result.rows == [(7, "Ada")]
assert catalog.find_column("student", "name").col_type == "VARCHAR"
close_database(catalog, storage)
""" % str(tmp_path)
    for code in (writer, reader):
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=str(ROOT),
            env=env, capture_output=True, text=True, encoding="utf-8",
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout


def test_compiler_create_persists_json_and_is_visible_later(tmp_path):
    """阶段一 CREATE 写入 catalog.json，后续编译能看到该表。"""
    results, catalog, _ = _roundtrip(tmp_path, "CREATE TABLE Student(id INT);", "compiler")
    assert results[0].ok and results[0].exec_result is None
    path = tmp_path / "catalog.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["tables"][0]["name"] == "Student"
    later, _, _ = _roundtrip(tmp_path, "SELECT * FROM student;", "compiler")
    assert later[0].ok and later[0].semantic_ok


def test_compile_sql_snapshot_does_not_write_real_catalog(tmp_path):
    """纯编译建表只对本次快照可见。"""
    catalog = Catalog(json_path=str(tmp_path / "catalog.json"))
    results = compile_sql("CREATE TABLE t(id INT); SELECT * FROM t;", catalog)
    assert [item.ok for item in results] == [True, True]
    assert catalog.find_table("t") is None
    assert not (tmp_path / "catalog.json").exists()


def test_failed_create_is_not_registered(tmp_path):
    """失败的 CREATE 不得进入目录。"""
    sql = "CREATE TABLE student(id INT, id VARCHAR); SELECT * FROM student;"
    results, catalog, _ = _roundtrip(tmp_path, sql, "compiler")
    assert not results[0].ok
    assert results[0].error.startswith("[SEMANTIC]")
    assert not results[1].ok
    assert catalog.find_table("student") is None


def test_lexer_error_then_valid_statement(tmp_path):
    """词法失败段 tokens 为空，后续合法语句继续。"""
    sql = "@;\nCREATE TABLE t(id INT);"
    results, catalog, _ = _roundtrip(tmp_path, sql, "compiler")
    assert len(results) == 2
    assert not results[0].ok and results[0].tokens is None
    assert "[LEXER]" in results[0].error
    assert results[1].ok
    assert catalog.find_table("t") is not None


def test_missing_semicolon_fails_whole_segment(tmp_path):
    """缺分号使当前分段整体失败，下一句独立成功。"""
    sql = "CREATE TABLE t(id INT);\nSELECT * FROM t\nSELECT * FROM t;"
    results, _, _ = _roundtrip(tmp_path, sql, "compiler")
    assert len(results) == 2
    assert results[0].ok
    assert not results[1].ok
    assert "expected: ';'" in results[1].error


def test_insert_dual_type_errors_are_collected(tmp_path):
    """INSERT 两个类型错误要一次收集完整。"""
    sql = ("CREATE TABLE student(id INT, name VARCHAR);"
           "INSERT INTO student(id, name) VALUES('Alice', 1);")
    results, _, _ = _roundtrip(tmp_path, sql, "compiler")
    assert results[0].ok and not results[1].ok
    assert results[1].ast is not None and results[1].plans == []
    assert results[1].error.count("[SEMANTIC]") == 2


def test_and_or_plan_and_filter_true_optimization(tmp_path):
    """AND 绑定更紧；1=1 AND 10+8 应折叠。"""
    sql = """
    CREATE TABLE t(a INT, b INT, c INT);
    SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3;
    SELECT * FROM t WHERE 1 = 1 AND a > 10 + 8;
    """
    results, _, _ = _roundtrip(tmp_path, sql, "compiler")
    assert all(item.ok for item in results)
    first_where = results[1].ast[0]["where"]
    assert first_where["op"] == "OR"
    assert first_where["right"]["op"] == "AND"
    opt = results[2].plans[1]
    assert opt["child"]["op"] == "Filter"
    predicate = opt["child"]["predicate"]
    assert predicate["op"] == ">"
    assert predicate["right"]["value"] == 18


def test_empty_table_does_not_raise_on_div_zero_predicate(tmp_path):
    """空表不求值 WHERE，即使谓词除零。"""
    sql = "CREATE TABLE t(id INT); SELECT * FROM t WHERE 1 / 0 = 1;"
    results, _, _ = _roundtrip(tmp_path, sql, "database")
    assert results[1].ok
    assert results[1].exec_result.rows == []
    assert results[1].exec_result.columns == ["id"]


def test_div_zero_on_nonempty_table_is_execute_error(tmp_path):
    """有行时除零必须是执行错误，且保留两份计划。"""
    sql = ("CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);"
           "SELECT * FROM t WHERE 1 / 0 = 1;")
    results, _, _ = _roundtrip(tmp_path, sql, "database")
    assert results[0].ok and results[1].ok and not results[2].ok
    assert results[2].error == "[EXECUTE] Error: division by zero"
    assert len(results[2].plans) == 2


def test_cli_database_empty_query_and_switches(tmp_path):
    """空结果仍有 COLUMNS；关闭开关不再打印对应段。"""
    sql = "CREATE TABLE student(id INT, name VARCHAR); SELECT * FROM student;"
    completed = _cli(tmp_path, sql, extra=["--no-tokens", "--no-ast", "--no-plan", "--no-opt-plan"])
    assert completed.returncode == 0, completed.stderr
    output = completed.stdout.replace("\r\n", "\n")
    assert "TOKENS:" not in output and "AST:" not in output
    assert "PLAN:" not in output and "OPT_PLAN:" not in output
    assert "SEMANTIC: OK" in output
    assert 'COLUMNS: ["id","name"]' in output
    assert "RESULT: 0 rows selected" in output
    assert "ROW:" not in output


def test_cli_empty_input_exits_zero(tmp_path):
    """空输入无输出且退出 0。"""
    completed = _cli(tmp_path, "")
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_cli_file_and_stdin_are_equivalent(tmp_path):
    """文件与标准输入应得到相同 stdout 和退出码。"""
    sql = "CREATE TABLE t(id INT);"
    sql_file = tmp_path / "in.sql"
    sql_file.write_text(sql, encoding="utf-8")
    via_file = _cli(tmp_path / "file-db", sql, sql_file=sql_file)
    via_stdin = _cli(tmp_path / "stdin-db", sql)
    assert via_file.returncode == via_stdin.returncode == 0
    assert via_file.stdout == via_stdin.stdout


def test_unclosed_string_is_lexer_error(tmp_path):
    """未闭合字符串按词法失败处理。"""
    results, _, _ = _roundtrip(tmp_path, "SELECT 'oops;", "compiler")
    assert not results[0].ok
    assert results[0].tokens is None
    assert "[LEXER]" in results[0].error


def test_compile_sql_raises_on_first_failure():
    """纯编译接口遇到首个失败段抛原始异常。"""
    catalog = Catalog()
    with pytest.raises(ParserError):
        compile_sql(";", catalog)
    with pytest.raises(SemanticError):
        compile_sql("SELECT * FROM missing;", catalog)
