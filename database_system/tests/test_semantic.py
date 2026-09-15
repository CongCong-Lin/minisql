"""C 的语义测试直接构造公共 AST，不依赖尚未完成的 Parser。"""

from copy import deepcopy

import pytest

from sql_compiler.ast_nodes import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, IdentifierExpr,
    InsertStmt, LiteralExpr, SelectStmt, UnaryExpr,
)
from sql_compiler.catalog import Catalog
from sql_compiler.errors import SemanticError
from sql_compiler.semantic import analyze


def literal(value, kind="INT", column=20):
    return LiteralExpr(value, kind, line=2, column=column)


def binary(op, left, right):
    return BinaryExpr(op, left, right, line=2, column=15)


def select(where=None, columns="*", table="student"):
    return SelectStmt(columns, table, where, line=2, column=1)


def insert(columns, values):
    return InsertStmt("student", columns, values, line=2, column=1)


@pytest.fixture
def catalog():
    result = Catalog()
    result.create_table("Student", [
        ColumnDef("Id", "INT", line=1, column=22),
        ColumnDef("Name", "VARCHAR", line=1, column=30),
    ])
    return result


def test_success_preserves_structure_and_does_not_commit(catalog, monkeypatch):
    identifier = IdentifierExpr("iD", line=2, column=10)
    where = binary("AND", binary(">", identifier, literal(1)),
                   UnaryExpr("NOT", literal(False, "BOOL"), line=2, column=40))
    stmts = [select(where, ["NAME", "id"]), DeleteStmt(
        "STUDENT", literal(True, "BOOL"), line=3, column=1)]
    before = [stmt.to_dict() for stmt in stmts]

    def forbidden(*args):
        pytest.fail("语义分析不能提交目录")

    monkeypatch.setattr(catalog, "create_table", forbidden)
    assert analyze(stmts, catalog) is stmts
    assert [stmt.to_dict() for stmt in stmts] == before
    assert identifier.resolved_type == identifier.expr_type == "INT"
    assert where.expr_type == "BOOL"
    assert where.left.expr_type == "BOOL"
    assert catalog.list_tables() == ["Student"]


def test_create_never_registers_even_with_later_statement(tmp_path):
    path = tmp_path / "catalog.json"
    catalog = Catalog(str(path))
    create = CreateTableStmt("t", [ColumnDef("a", "INT", line=1, column=16)],
                             line=1, column=1)
    assert analyze([create], catalog) == [create]
    assert catalog.list_tables() == [] and not path.exists()
    with pytest.raises(SemanticError, match="表不存在"):
        analyze([create, select(table="t")], catalog)
    assert catalog.list_tables() == [] and not path.exists()


def test_duplicate_create_locations(catalog):
    duplicate = CreateTableStmt("STUDENT", [
        ColumnDef("a", "INT", line=7, column=20)], line=7, column=3)
    with pytest.raises(SemanticError) as caught:
        analyze([duplicate], catalog)
    assert (caught.value.line, caught.value.column) == (7, 3)
    duplicate = CreateTableStmt("t", [
        ColumnDef("a", "INT", line=3, column=12),
        ColumnDef("A", "INT", line=3, column=20)], line=3, column=1)
    with pytest.raises(SemanticError) as caught:
        analyze([duplicate], catalog)
    assert (caught.value.line, caught.value.column) == (3, 20)


@pytest.mark.parametrize("stmt,position", [
    (select(table="missing"), (2, 1)),
    (select(columns=["missing"]), (2, 1)),
    (select(binary("=", IdentifierExpr("missing", line=2, column=33), literal(1))),
     (2, 33)),
])
def test_name_binding_failures(catalog, stmt, position):
    with pytest.raises(SemanticError) as caught:
        analyze([deepcopy(stmt)], catalog)
    assert (caught.value.line, caught.value.column) == position


@pytest.mark.parametrize("stmt", [
    select(table="__CATALOG__"),
    CreateTableStmt("__catalog__", [ColumnDef("a", "INT", line=2, column=20)],
                    line=2, column=1),
    InsertStmt("__catalog__", ["a"], [literal(1)], line=2, column=1),
    DeleteStmt("__catalog__", None, line=2, column=1),
])
def test_system_table_is_hidden(catalog, stmt):
    with pytest.raises(SemanticError, match="系统目录"):
        analyze([stmt], catalog)


def test_insert_reordering_is_valid_but_not_applied(catalog):
    stmt = insert(["NAME", "id"], [literal("张三", "VARCHAR"), literal(7)])
    before = stmt.to_dict()
    analyze([stmt], catalog)
    assert stmt.to_dict() == before
    assert [value.expr_type for value in stmt.values] == ["VARCHAR", "INT"]


@pytest.mark.parametrize("columns,values,reason", [
    (["id", "name"], [literal(1)], "列数"),
    (["id", "ID"], [literal(1), literal(2)], "重复"),
    (["id"], [literal(1)], "完整覆盖"),
    (["missing", "name"], [literal(1), literal("a", "VARCHAR")], "不存在"),
    ([], [], "完整覆盖"),
])
def test_insert_structure_precedes_values(catalog, columns, values, reason):
    with pytest.raises(SemanticError, match=reason) as caught:
        analyze([insert(columns, values)], catalog)
    assert caught.value.errors == []


def test_insert_reports_both_wrong_values(catalog):
    stmt = insert(["id", "name"], [
        literal("Alice", "VARCHAR", 37), literal(1, "INT", 45)])
    with pytest.raises(SemanticError) as caught:
        analyze([stmt], catalog)
    errors = caught.value.errors
    assert len(errors) == 2
    assert [(e.line, e.column) for e in errors] == [(2, 37), (2, 45)]
    assert errors[0].reason == "列 id 需要 INT，实际为 VARCHAR"
    assert errors[1].reason == "列 name 需要 VARCHAR，实际为 INT"
    assert all(not error.errors for error in errors)


def test_one_value_can_have_both_type_and_length_errors(catalog):
    stmt = insert(["id", "name"], [
        literal("x" * 256, "VARCHAR", 30), literal("a", "VARCHAR", 40)])
    with pytest.raises(SemanticError) as caught:
        analyze([stmt], catalog)
    assert len(caught.value.errors) == 2
    assert [e.column for e in caught.value.errors] == [30, 30]
    assert caught.value.errors[1].reason == "varchar literal exceeds 255 bytes"


@pytest.mark.parametrize("value,valid", [
    ("x" * 255, True), ("x" * 256, False), ("汉" * 85, True), ("汉" * 85 + "x", False),
])
@pytest.mark.parametrize("context", ["insert", "where"])
def test_varchar_limit_counts_utf8_bytes(catalog, value, valid, context):
    expr = literal(value, "VARCHAR", 32)
    stmt = (insert(["id", "name"], [literal(1), expr]) if context == "insert"
            else select(binary("=", IdentifierExpr("name", line=2, column=10), expr)))
    if valid:
        analyze([stmt], catalog)
    else:
        with pytest.raises(SemanticError) as caught:
            analyze([stmt], catalog)
        assert caught.value.reason == "varchar literal exceeds 255 bytes"
        assert caught.value.column == 32


@pytest.mark.parametrize("where", [
    literal(1), literal("a", "VARCHAR"), literal(1.5, "FLOAT"),
    binary(">", literal(True, "BOOL"), literal(False, "BOOL")),
    binary("+", literal(1.0, "FLOAT"), literal("bad", "VARCHAR")),
    binary("=", literal(1), literal("1", "VARCHAR")),
    binary("OR", literal(True, "BOOL"), binary("+", literal("x", "VARCHAR"), literal(1))),
    UnaryExpr("NOT", literal(1), line=2, column=10),
])
def test_invalid_types_including_unreached_branch(catalog, where):
    with pytest.raises(SemanticError):
        analyze([select(deepcopy(where))], catalog)


@pytest.mark.parametrize("value,kind", [(True, "BOOL"), (1.0, "FLOAT")])
def test_no_implicit_insert_conversion(catalog, value, kind):
    with pytest.raises(SemanticError):
        analyze([insert(["id", "name"], [literal(value, kind), literal("x", "VARCHAR")])],
                catalog)


@pytest.mark.parametrize("arithmetic", [
    binary("/", literal(1), literal(0)),
    binary("+", literal(2147483647), literal(1)),
])
def test_semantic_analysis_does_not_evaluate_arithmetic(catalog, arithmetic):
    where = binary("=", arithmetic, literal(1))
    analyze([select(where)], catalog)
    assert arithmetic.expr_type == "INT" and where.expr_type == "BOOL"


def test_empty_input(catalog):
    stmts = []
    assert analyze(stmts, catalog) is stmts


@pytest.mark.parametrize("shape", ["left", "right", "logical", "unary"])
def test_deep_expression_uses_no_python_recursion(catalog, shape):
    """长链直接检验 C 入口，不调用仍由 B 负责的递归序列化。"""
    kind = "INT" if shape in {"left", "right"} else "BOOL"
    leaf = literal(1 if kind == "INT" else True, kind)
    expr = leaf
    nodes = [leaf]
    edges = []
    for index in range(1500):
        if shape == "unary":
            child = expr
            expr = UnaryExpr("NOT", child, line=2, column=index + 30)
            edges.append((expr, "operand", child))
        else:
            value = literal(1 if kind == "INT" else True, kind, index + 30)
            left, right = (value, expr) if shape == "right" else (expr, value)
            expr = BinaryExpr("+" if kind == "INT" else "AND", left, right,
                              line=2, column=index + 30)
            edges.extend(((expr, "left", left), (expr, "right", right)))
            nodes.append(value)
        nodes.append(expr)
    where = binary("=", expr, literal(1501)) if kind == "INT" else expr
    stmts = [select(where)]
    assert analyze(stmts, catalog) is stmts
    assert all(item.expr_type == kind for item in nodes)
    assert where.expr_type == "BOOL"
    assert all(getattr(parent, field) is child for parent, field, child in edges)


def test_deep_expression_reports_semantic_error_at_operator(catalog):
    """完成长链类型推导后仍应报告外层比较的类型错误。"""
    expr = literal(1)
    for _ in range(1500):
        expr = binary("+", expr, literal(1))
    where = BinaryExpr("=", expr, literal("wrong", "VARCHAR"), line=8, column=17)
    with pytest.raises(SemanticError) as caught:
        analyze([select(where)], catalog)
    assert (caught.value.line, caught.value.column) == (8, 17)
    assert caught.value.reason == "运算符 = 不支持类型 INT 和 VARCHAR"
    assert expr.expr_type == "INT"


def test_expression_checks_left_error_before_right(catalog):
    """改成显式栈后仍然先处理左子树，不覆盖首个诊断。"""
    left = IdentifierExpr("missing_left", line=2, column=20)
    right = IdentifierExpr("missing_right", line=2, column=40)
    with pytest.raises(SemanticError) as caught:
        analyze([select(binary("=", left, right))], catalog)
    assert caught.value.reason == "列不存在：missing_left"
    assert caught.value.column == 20
    assert right.resolved_type is right.expr_type is None
