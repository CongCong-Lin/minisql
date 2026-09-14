"""D 负责：表达式求值及短路行为。"""

from sql_compiler import types as type_rules
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError, IntegerArithmeticError

_CMP_FUNCS = {
    "=": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
}


def evaluate(expr: dict, row: tuple, columns: list[ColumnDef]) -> object:
    """依据列序求值，不访问 Catalog 或存储。"""
    kind = expr.get("node")
    if kind == "BoundColumnExpr":
        index = expr.get("index")
        if type(index) is not int or not 0 <= index < len(row):
            raise ExecuteError("invalid bound column index")
        return row[index]
    if kind == "LiteralExpr":
        return expr["value"]
    if kind == "IdentifierExpr":
        return row[_column_index(columns, expr["name"])]
    if kind == "UnaryExpr":
        if expr.get("op") != "NOT":
            raise ExecuteError(f"unsupported unary operator '{expr.get('op')}'")
        operand = evaluate(expr["operand"], row, columns)
        return None if operand is None else not operand
    if kind == "BinaryExpr":
        return _eval_binary(expr, row, columns)
    raise ExecuteError(f"unsupported expression node '{kind}'")


def _eval_binary(expr: dict, row: tuple, columns: list[ColumnDef]) -> object:
    """按运算符求值；AND/OR 从左到右短路。"""
    op = expr["op"]
    if op == "AND":
        left = evaluate(expr["left"], row, columns)
        if left is False:
            return False
        right = evaluate(expr["right"], row, columns)
        if right is False:
            return False
        return None if left is None or right is None else right
    if op == "OR":
        left = evaluate(expr["left"], row, columns)
        if left is True:
            return True
        right = evaluate(expr["right"], row, columns)
        if right is True:
            return True
        return None if left is None or right is None else right
    left = evaluate(expr["left"], row, columns)
    right = evaluate(expr["right"], row, columns)
    if left is None or right is None:
        return None
    if op in {"+", "-", "*", "/"}:
        if expr.get("value_type") in {"BIGINT", "FLOAT"}:
            from engine.query_numbers import query_arithmetic
            return query_arithmetic(op, left, right, expr["value_type"])
        try:
            return type_rules.checked_int_arithmetic(op, left, right)
        except IntegerArithmeticError as exc:
            raise ExecuteError(exc.reason) from exc
    if op in _CMP_FUNCS:
        return _CMP_FUNCS[op](left, right)
    raise ExecuteError(f"unsupported binary operator '{op}'")


def _column_index(columns: list[ColumnDef], name: str) -> int:
    """按小写列名定位行内下标。"""
    key = name.lower()
    for index, column in enumerate(columns):
        if column.name.lower() == key:
            return index
    raise ExecuteError(f"column '{name}' does not exist")
