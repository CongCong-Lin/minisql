"""D 负责：计划生成的结构与边界。"""

import pytest

from sql_compiler.ast_nodes import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, IdentifierExpr,
    InsertStmt, LiteralExpr, SelectStmt, UnaryExpr,
)
from sql_compiler.errors import PlannerError
from sql_compiler.planner import plan
from tests.d_support import FakeCatalog


def test_create_table_plan_keeps_column_positions():
    """CreateTable 列描述使用 type 字段并保留定义位置。"""
    stmt = CreateTableStmt(
        "Student",
        [ColumnDef("id", "INT", line=1, column=22),
         ColumnDef("name", "VARCHAR", line=1, column=31)],
        line=1, column=1,
    )
    result = plan(stmt, FakeCatalog())
    assert result == {
        "op": "CreateTable",
        "table": "Student",
        "columns": [
            {"name": "id", "type": "INT", "line": 1, "column": 22},
            {"name": "name", "type": "VARCHAR", "line": 1, "column": 31},
        ],
    }


def test_insert_plan_uses_native_values_in_sql_order():
    """INSERT 的 values 必须是 Python 原生值，保持 SQL 目标列顺序。"""
    stmt = InsertStmt(
        "student",
        ["name", "id"],
        [LiteralExpr("Alice", "VARCHAR", line=1, column=40),
         LiteralExpr(1, "INT", line=1, column=49)],
        line=1, column=1,
    )
    result = plan(stmt, FakeCatalog())
    assert result["op"] == "Insert"
    assert result["columns"] == ["name", "id"]
    assert result["values"] == ["Alice", 1]
    assert all(not isinstance(value, dict) for value in result["values"])


def test_select_star_without_where_is_project_over_scan():
    """无 WHERE 的 SELECT * 为 Project → SeqScan，columns 为字符串 *。"""
    stmt = SelectStmt("*", "student", None, line=1, column=1)
    result = plan(stmt, FakeCatalog())
    assert result == {
        "op": "Project",
        "columns": "*",
        "child": {"op": "SeqScan", "table": "student"},
    }


def test_select_where_wraps_filter_and_reuses_ast_dict():
    """带 WHERE 时外包 Filter，谓词复用 AST 结构字段。"""
    predicate = BinaryExpr(
        "=", IdentifierExpr("id", line=1, column=32),
        LiteralExpr(1, "INT", line=1, column=35),
        line=1, column=33,
    )
    stmt = SelectStmt(["ID"], "T", predicate, line=1, column=1)
    result = plan(stmt, FakeCatalog())
    assert result["op"] == "Project"
    assert result["columns"] == ["ID"]
    assert result["child"]["op"] == "Filter"
    assert result["child"]["predicate"]["node"] == "BinaryExpr"
    assert "expr_type" not in result["child"]["predicate"]
    assert result["child"]["child"] == {"op": "SeqScan", "table": "T"}


def test_delete_with_not_predicate():
    """DELETE 恒为 Delete → 可选 Filter → SeqScan。"""
    where = UnaryExpr(
        "NOT", LiteralExpr(False, "BOOL", line=1, column=25),
        line=1, column=21,
    )
    stmt = DeleteStmt("student", where, line=1, column=1)
    result = plan(stmt, FakeCatalog())
    assert result["op"] == "Delete"
    assert result["table"] == "student"
    assert result["child"]["op"] == "Filter"
    assert result["child"]["predicate"]["op"] == "NOT"


def test_unknown_statement_raises_planner_error():
    """未知语句必须抛出 PlannerError，而不是返回空计划。"""
    from sql_compiler.ast_nodes import Stmt

    class Mystery(Stmt):
        pass

    mystery = Mystery(line=3, column=5)
    with pytest.raises(PlannerError) as caught:
        plan(mystery, FakeCatalog())
    assert caught.value.stage == "PLANNER"
    assert "Mystery" in caught.value.reason
