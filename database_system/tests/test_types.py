"""C 的类型组合与整数边界；直接验证契约，不依赖执行器。"""

from itertools import product

import pytest

from sql_compiler.errors import IntegerArithmeticError
from sql_compiler.types import (
    INT_MAX, INT_MIN, checked_int_arithmetic, expression_type, insert_type_matches,
)


@pytest.mark.parametrize("op,left,right", list(product(
    ["+", "-", "*", "/", "=", "!=", ">", ">=", "<", "<=", "AND", "OR"],
    ["INT", "VARCHAR", "FLOAT", "BOOL"], ["INT", "VARCHAR", "FLOAT", "BOOL"],
)))
def test_type_matrix(op, left, right):
    """枚举契约中的合法行，其他组合必须拒绝。"""
    arithmetic = {(symbol, a, b): "FLOAT" if "FLOAT" in {a, b} else "INT"
                  for symbol in "+-*/" for a in ("INT", "FLOAT") for b in ("INT", "FLOAT")}
    comparisons = {(symbol, kind, kind): "BOOL"
                   for symbol in ("=", "!=", ">", ">=", "<", "<=")
                   for kind in ("INT", "VARCHAR")}
    comparisons.update({(symbol, a, b): "BOOL" for symbol in ("=", "!=", ">", ">=", "<", "<=")
                        for a in ("INT", "FLOAT") for b in ("INT", "FLOAT")})
    comparisons.update({(symbol, "BOOL", "BOOL"): "BOOL" for symbol in ("=", "!=")})
    logical = {("AND", "BOOL", "BOOL"): "BOOL", ("OR", "BOOL", "BOOL"): "BOOL"}
    assert expression_type(op, left, right) == (arithmetic | comparisons | logical).get(
        (op, left, right))


@pytest.mark.parametrize("kind", ["INT", "VARCHAR", "FLOAT", "BOOL"])
def test_not_and_insert_rules(kind):
    assert expression_type("NOT", kind) == ("BOOL" if kind == "BOOL" else None)
    assert expression_type("NOT", kind, kind) is None
    assert insert_type_matches("INT", kind) == (kind == "INT")
    assert insert_type_matches("VARCHAR", kind) == (kind == "VARCHAR")
    assert insert_type_matches("BOOL", kind) == (kind == "BOOL")


@pytest.mark.parametrize("op,left,right,wanted", [
    ("+", INT_MAX, 0, INT_MAX), ("-", INT_MIN, 0, INT_MIN),
    ("*", INT_MIN, 1, INT_MIN), ("/", -3, 2, -1), ("/", 3, -2, -1),
    ("/", -3, -2, 1), ("/", -1, 2, 0), ("/", INT_MIN, 1, INT_MIN),
    ("-", 0, INT_MAX, -INT_MAX), ("*", 46340, 46340, 2147395600),
])
def test_checked_integer_results(op, left, right, wanted):
    assert checked_int_arithmetic(op, left, right) == wanted


@pytest.mark.parametrize("op,left,right,reason", [
    ("/", 1, 0, "division by zero"),
    ("+", INT_MAX, 1, "integer arithmetic out of range"),
    ("-", INT_MIN, 1, "integer arithmetic out of range"),
    ("*", 46341, 46341, "integer arithmetic out of range"),
    ("/", INT_MIN, -1, "integer arithmetic out of range"),
    ("+", INT_MAX + 1, -1, "integer arithmetic out of range"),
])
def test_checked_integer_errors(op, left, right, reason):
    with pytest.raises(IntegerArithmeticError) as caught:
        checked_int_arithmetic(op, left, right)
    assert caught.value.reason == reason


@pytest.mark.parametrize("op,left,right", [("+", True, 1), ("+", 1, 2.0), ("%", 1, 2)])
def test_arithmetic_rejects_invalid_direct_calls(op, left, right):
    with pytest.raises(IntegerArithmeticError):
        checked_int_arithmetic(op, left, right)
