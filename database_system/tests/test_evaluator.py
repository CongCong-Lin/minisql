"""D 负责：表达式求值、短路和算术错误。"""

import pytest

from engine.evaluator import evaluate
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from tests.d_support import binary, ensure_arithmetic, ident, lit, unary

COLUMNS = [
    ColumnDef("id", "INT", line=1, column=1),
    ColumnDef("name", "VARCHAR", line=1, column=8),
]
ROW = (3, "Ada")


@pytest.fixture(autouse=True)
def _arithmetic(monkeypatch):
    ensure_arithmetic(monkeypatch)


def test_identifier_lookup_is_case_insensitive():
    """列名按小写比较，取值使用行下标。"""
    assert evaluate(ident("ID", 1, 1), ROW, COLUMNS) == 3
    assert evaluate(ident("Name", 1, 1), ROW, COLUMNS) == "Ada"


def test_and_or_short_circuit_skips_error_branch():
    """FALSE AND 除零、TRUE OR 除零都不得求值右支。"""
    div0 = binary("/", lit(1, "INT", 1, 10), lit(0, "INT", 1, 12), 1, 11)
    assert evaluate(binary("AND", lit(False, "BOOL", 1, 1), div0, 1, 7), ROW, COLUMNS) is False
    assert evaluate(binary("OR", lit(True, "BOOL", 1, 1), div0, 1, 6), ROW, COLUMNS) is True


def test_and_or_evaluate_right_when_needed():
    """需要求值右支时，除零必须变成 ExecuteError。"""
    div0 = binary("/", lit(1, "INT", 1, 10), lit(0, "INT", 1, 12), 1, 11)
    with pytest.raises(ExecuteError, match="division by zero"):
        evaluate(binary("AND", lit(True, "BOOL", 1, 1), div0, 1, 6), ROW, COLUMNS)
    with pytest.raises(ExecuteError, match="division by zero"):
        evaluate(binary("OR", lit(False, "BOOL", 1, 1), div0, 1, 7), ROW, COLUMNS)


def test_division_truncates_toward_zero():
    """整数除法向零截断，例如 (0-3)/2 为 -1。"""
    expr = binary(
        "/",
        binary("-", lit(0, "INT", 1, 2), lit(3, "INT", 1, 4), 1, 3),
        lit(2, "INT", 1, 7),
        1, 6,
    )
    assert evaluate(expr, ROW, COLUMNS) == -1


def test_overflow_becomes_execute_error():
    """每一步算术越界都转为执行错误。"""
    expr = binary("+", lit(2147483647, "INT", 1, 1), lit(1, "INT", 1, 13), 1, 12)
    with pytest.raises(ExecuteError, match="integer arithmetic out of range"):
        evaluate(expr, ROW, COLUMNS)


def test_not_and_comparison():
    """NOT 与比较运算返回布尔值。"""
    expr = unary(
        "NOT",
        binary("=", ident("id", 1, 8), lit(3, "INT", 1, 11), 1, 10),
        1, 4,
    )
    assert evaluate(expr, ROW, COLUMNS) is False
