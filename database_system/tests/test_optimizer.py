"""D 负责：常量折叠与布尔化简。"""

from copy import deepcopy

import pytest

from sql_compiler.optimizer import optimize
from tests.d_support import binary, ensure_arithmetic, ident, lit, unary


@pytest.fixture(autouse=True)
def _arithmetic(monkeypatch):
    ensure_arithmetic(monkeypatch)


def _filter_plan(predicate: dict) -> dict:
    return {
        "op": "Project",
        "columns": "*",
        "child": {
            "op": "Filter",
            "predicate": predicate,
            "child": {"op": "SeqScan", "table": "student"},
        },
    }


def test_acceptance_filter_and_arithmetic_fold():
    """Filter[1=1 AND age>10+8] 应得到 Filter[age>18] 并记录两种规则。"""
    original = _filter_plan(binary(
        "AND",
        binary("=", lit(1, "INT", 1, 8), lit(1, "INT", 1, 10), 1, 9),
        binary(
            ">", ident("age", 1, 16),
            binary("+", lit(10, "INT", 1, 20), lit(8, "INT", 1, 23), 1, 22),
            1, 19,
        ),
        1, 12,
    ))
    backup = deepcopy(original)
    result, rules = optimize(original)
    assert original == backup
    assert result is not original
    assert rules == ["constant_folding", "boolean_simplification"]
    predicate = result["child"]["predicate"]
    assert predicate["op"] == ">"
    assert predicate["left"]["name"] == "age"
    assert predicate["right"] == lit(18, "INT", 1, 22)


def test_division_by_zero_is_not_folded():
    """除零子树必须保留，不能提前变成编译错误。"""
    original = _filter_plan(binary(
        "=",
        binary("/", lit(1, "INT", 1, 8), lit(0, "INT", 1, 10), 1, 9),
        lit(1, "INT", 1, 14),
        1, 12,
    ))
    result, rules = optimize(original)
    assert rules == []
    assert result["child"]["predicate"]["left"]["op"] == "/"


def test_overflow_is_not_folded():
    """溢出子树同样保留原子树。"""
    original = _filter_plan(binary(
        "+", lit(2147483647, "INT", 1, 8), lit(1, "INT", 1, 20), 1, 19,
    ))
    result, rules = optimize(original)
    assert rules == []
    assert result["child"]["op"] == "Filter"
    assert result["child"]["predicate"]["op"] == "+"


def test_and_false_keeps_arithmetic_subtree():
    """x AND FALSE 在 x 含算术时不能消除，以免改变错误时点。"""
    original = _filter_plan(binary(
        "AND",
        binary("/", lit(1, "INT", 1, 8), ident("n", 1, 10), 1, 9),
        lit(False, "BOOL", 1, 16),
        1, 14,
    ))
    result, rules = optimize(original)
    assert "boolean_simplification" not in rules
    assert result["child"]["predicate"]["op"] == "AND"


def test_false_and_x_can_drop_arithmetic():
    """FALSE AND x 原式短路，可以折叠为 FALSE。"""
    original = _filter_plan(binary(
        "AND",
        lit(False, "BOOL", 1, 8),
        binary("/", lit(1, "INT", 1, 16), lit(0, "INT", 1, 18), 1, 17),
        1, 13,
    ))
    result, rules = optimize(original)
    assert rules == ["boolean_simplification"]
    assert result["child"]["predicate"] == lit(False, "BOOL", 1, 8)


def test_filter_true_is_removed():
    """Filter[TRUE] 应变为子计划，且不消除 Project[*]。"""
    original = _filter_plan(lit(True, "BOOL", 1, 20))
    result, rules = optimize(original)
    assert rules == ["boolean_simplification"]
    assert result["op"] == "Project"
    assert result["columns"] == "*"
    assert result["child"] == {"op": "SeqScan", "table": "student"}


def test_filter_false_is_kept():
    """Filter[FALSE] 必须保留，避免改变扫描行为。"""
    original = {
        "op": "Delete",
        "table": "student",
        "child": {
            "op": "Filter",
            "predicate": lit(False, "BOOL", 1, 28),
            "child": {"op": "SeqScan", "table": "student"},
        },
    }
    result, rules = optimize(original)
    assert rules == []
    assert result["child"]["op"] == "Filter"


def test_not_true_folds_to_false():
    """常量 NOT 属于布尔化简。"""
    original = _filter_plan(unary("NOT", lit(True, "BOOL", 1, 25), 1, 21))
    result, rules = optimize(original)
    assert rules == ["boolean_simplification"]
    assert result["child"]["predicate"] == lit(False, "BOOL", 1, 21)


def test_varchar_comparison_folds_with_python_order():
    """VARCHAR 比较按区分大小写的 Python 字典序折叠。"""
    original = _filter_plan(binary(
        "<", lit("a", "VARCHAR", 1, 8), lit("A", "VARCHAR", 1, 14), 1, 12,
    ))
    result, rules = optimize(original)
    assert rules == ["constant_folding"]
    assert result["child"]["op"] == "Filter"
    assert result["child"]["predicate"]["value"] is False
