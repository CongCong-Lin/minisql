"""MiniSQL Studio 桌面客户端：会话与窗口烟测。"""

from __future__ import annotations

import pytest

from studio.inspect import inspect_sql
from studio.session import StudioSession

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
        names = [app.tree.item(item, "text") for item in app.tree.get_children("")]
        assert any("MiniSQL" in name for name in names)
        kept_sql = app.sql.get("1.0", "end-1c")
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
    finally:
        try:
            app._on_close()
        except Exception:
            pass
