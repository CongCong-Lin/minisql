"""MiniSQL Studio 桌面客户端：会话与窗口烟测。"""

from __future__ import annotations

import pytest

from studio.inspect import inspect_sql
from studio.session import StudioSession, format_cell, format_plan_tree, token_span

def test_inspect_sql_marks_unknown_table_and_keeps_catalog():
    diags = inspect_sql("SELECT * FROM nosuch;")
    assert diags
    assert diags[0].stage == "SEMANTIC"
    assert diags[0].line == 1
    assert diags[0].start == "1.14" or "nosuch" in diags[0].reason


def test_inspect_sql_uses_buffer_create_without_writing(tmp_path):
    session = StudioSession()
    session.open(str(tmp_path), mode="database")
    diags = session.inspect(
        "CREATE TABLE t(id INT); SELECT * FROM t;"
    )
    assert diags == []
    assert session.tables() == []
    session.close()


def test_inspect_sql_parser_error_has_span():
    diags = inspect_sql("SELECT FROM student;")
    assert diags
    assert diags[0].stage == "PARSER"
    start_line, start_col = diags[0].start.split(".")
    end_line, end_col = diags[0].end.split(".")
    assert start_line == end_line == "1"
    assert int(end_col) > int(start_col)


def test_session_crud_and_artifacts(tmp_path):
    session = StudioSession()
    session.open(str(tmp_path), mode="database")
    payload = session.run_sql(
        "CREATE TABLE student(id INT, name VARCHAR);"
        "INSERT INTO student(id, name) VALUES(1, 'Ada');"
        "SELECT * FROM student;"
    )
    assert payload["sql_failed"] is False
    query = next(item for item in payload["results"] if item["exec"] and item["exec"]["is_query"])
    assert query["exec"]["rows"] == [[1, "Ada"]]
    assert query["tokens"]
    assert query["ast"]
    assert query["plan"]
    assert query["opt_plan"]
    assert session.tables()[0]["name"] == "student"
    session.close()
    assert session.connected is False


def test_session_compiler_mode(tmp_path):
    session = StudioSession()
    session.open(str(tmp_path), mode="compiler")
    payload = session.run_sql(
        "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(7); SELECT * FROM t WHERE id = 7;"
    )
    assert payload["sql_failed"] is False
    assert all(item["ok"] for item in payload["results"])
    assert payload["results"][0]["plan"]["op"] == "CreateTable"
    assert payload["results"][2]["opt_plan"]["op"] == "Project"
    assert payload["results"][2]["exec"] is None  # compiler 模式不执行 DML/查询
    assert session.tables()[0]["name"] == "t"
    assert (tmp_path / "catalog.json").is_file()
    session.close()


def test_desktop_window_crud(tmp_path):
    pytest.importorskip("tkinter")
    try:
        from studio.app import StudioApp
        app = StudioApp()
    except Exception as exc:
        pytest.skip(f"当前环境无法创建 Tk 窗口: {exc}")
    try:
        app.withdraw()
        app.dir_var.set(str(tmp_path))
        app.connect()
        assert app.session.connected
        app._replace_sql(
            "CREATE TABLE student(id INT, name VARCHAR);\n"
            "INSERT INTO student(id, name) VALUES(1, 'Ada');\n"
            "SELECT * FROM student;"
        )
        app.execute()
        tabs = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
        assert "Result 3" in tabs
        assert "Messages" in tabs
        assert "Tokens" in tabs
        ranges = app.sql.tag_ranges("sql-result")
        assert ranges
        assert "SELECT * FROM student;" in app.sql.get(ranges[0], ranges[1])
        assert "CREATE TABLE" not in app.sql.get(ranges[0], ranges[1])
        names = [app.tree.item(item, "text") for item in app.tree.get_children("")]
        assert any("MiniSQL" in name for name in names)
        kept_sql = app.sql.get("1.0", "end-1c")
        app._replace_sql("SELECT id FROM student;\nSELECT name FROM student;")
        app.execute()
        tabs_map = {app.nb.tab(tab, "text"): tab for tab in app.nb.tabs()}
        assert app._result_tab_index[str(tabs_map["Result 1"])] == 0
        assert app._result_tab_index[str(tabs_map["Result 2"])] == 1
        app.nb.select(tabs_map["Result 2"])
        app._highlight_selected_result()
        ranges = app.sql.tag_ranges("sql-result")
        highlighted = app.sql.get(ranges[0], ranges[1])
        assert "SELECT name FROM student;" in highlighted
        assert "SELECT id FROM student;" not in highlighted
        app._replace_sql(kept_sql)
        table_item = None
        for root in app.tree.get_children(""):
            for child in app.tree.get_children(root):
                if "table" in app.tree.item(child, "tags"):
                    table_item = child
                    break
        assert table_item is not None
        assert app._table_from_item(table_item) == "student"
        app.open_table("student")
        tabs = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
        assert "表 student" in tabs
        assert app.sql.get("1.0", "end-1c") == kept_sql
        assert app.st_msg.cget("text") == "已打开表 student"
        query = app._last["results"][0]["exec"]
        assert query["rows"] == [[1, "Ada"]]
        app._replace_sql("SELECT * FROM nosuch;")
        app._inspect_now()
        assert app._diagnostics
        assert app._diagnostics[0].stage == "SEMANTIC"
        assert "nosuch" in app._diagnostics[0].reason
        assert app.sql.tag_ranges("sql-error")
        assert "发现" in app.inspect_text.get("1.0", "end-1c")
        first_sql = app.sql.get("1.0", "end-1c")
        app.new_query()
        assert len(app._queries) == 2
        assert app._queries[app._query_index]["title"] == "查询 2"
        assert app.sql.get("1.0", "end-1c") == ""
        assert app._queries[1]["payload"] is None
        assert app._last is None
        tabs = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
        assert tabs == ["Messages"]
        app._replace_sql("SELECT name FROM student;")
        app.execute()
        assert app._queries[1]["payload"]["results"][0]["exec"]["columns"] == ["name"]
        app.switch_query(0)
        assert app._queries[0]["payload"]["results"][0]["exec"]["rows"] == [[1, "Ada"]]
        tabs = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
        assert "表 student" in tabs
        app.switch_query(1)
        assert app._last["results"][0]["exec"]["columns"] == ["name"]
        tabs = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
        assert "Result 1" in tabs
        assert "SELECT name FROM student;" in app.sql.get("1.0", "end-1c")
        app.close_query(1)
        assert len(app._queries) == 1
        assert app.sql.get("1.0", "end-1c") == first_sql
        tabs = [app.nb.tab(tab, "text") for tab in app.nb.tabs()]
        assert "表 student" in tabs
    finally:
        try:
            app._on_close()
        except Exception:
            pass


def test_format_cell_and_plan_tree_helpers():
    assert format_cell(None) == "NULL"
    assert format_cell(True) == "TRUE"
    assert format_cell(False) == "FALSE"
    assert format_cell(1.5) == "1.5"
    span = token_span([
        {"type": "KEYWORD", "lexeme": "SELECT", "line": 2, "column": 1},
        {"type": "DELIMITER", "lexeme": ";", "line": 3, "column": 5},
        {"type": "EOF", "lexeme": "", "line": 3, "column": 6},
    ])
    assert span == {"start": "2.0", "end": "3.5"}
    tree = format_plan_tree({
        "op": "Project",
        "items": [{"label": "s.name"}],
        "child": {
            "op": "NestedLoopJoin",
            "left": {"op": "SeqScan", "table": "student"},
            "right": {"op": "SeqScan", "table": "course"},
        },
    })
    assert "NestedLoopJoin" in tree
    assert "[left]" in tree and "[right]" in tree
    assert "student" in tree and "course" in tree


def test_session_extensions_update_join_group_and_null(tmp_path):
    session = StudioSession()
    session.open(str(tmp_path), mode="database")
    setup = session.run_sql(
        "CREATE TABLE student(id INT, name VARCHAR, score INT, team VARCHAR);"
        "CREATE TABLE course(student_id INT, title VARCHAR);"
        "INSERT INTO student(id,name,score,team) VALUES(1,'Ada',85,'A');"
        "INSERT INTO student(id,name,score,team) VALUES(2,'Bob',92,'A');"
        "INSERT INTO course(student_id,title) VALUES(1,'数据库');"
        "INSERT INTO course(student_id,title) VALUES(2,'编译原理');"
    )
    assert setup["sql_failed"] is False
    updated = session.run_sql("UPDATE student SET score=score+5 WHERE team='A';")
    assert updated["results"][0]["ok"]
    assert "updated" in updated["results"][0]["exec"]["message"]
    ordered = session.run_sql("SELECT name,score FROM student ORDER BY score DESC,id ASC;")
    query = ordered["results"][0]
    assert query["exec"]["columns"] == ["name", "score"]
    assert query["exec"]["rows"][0] == ["Bob", 97]
    assert query["span"]["start"] == "1.0"
    assert query["plan"]["op"] == "Project"
    joined = session.run_sql(
        "SELECT s.name,c.title FROM student s JOIN course c ON s.id=c.student_id ORDER BY s.id;"
    )
    assert joined["results"][0]["exec"]["is_query"]
    assert any(row[0] == "Ada" for row in joined["results"][0]["exec"]["rows"])
    tree = format_plan_tree(joined["results"][0]["plan"])
    assert "NestedLoopJoin" in tree
    grouped = session.run_sql(
        "SELECT team,COUNT(*) AS n,AVG(score) AS mean FROM student GROUP BY team;"
    )
    assert grouped["results"][0]["exec"]["columns"] == ["team", "n", "mean"]
    empty = session.run_sql(
        "CREATE TABLE empty_table(value INT);"
        "SELECT COUNT(*),SUM(value),AVG(value) FROM empty_table;"
    )
    row = empty["results"][1]["exec"]["rows"][0]
    assert row[0] == 0
    assert row[1] is None and row[2] is None
    assert format_cell(row[1]) == "NULL"
    session.close()
