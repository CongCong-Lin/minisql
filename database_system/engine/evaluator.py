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
    if kind == "LiteralExpr":
        return expr["value"]
    if kind == "IdentifierExpr":
        return row[_column_index(columns, expr["name"])]
    if kind == "UnaryExpr":
        if expr.get("op") != "NOT":
            raise ExecuteError(f"unsupported unary operator '{expr.get('op')}'")
        return not evaluate(expr["operand"], row, columns)
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
        return evaluate(expr["right"], row, columns)
    if op == "OR":
        left = evaluate(expr["left"], row, columns)
        if left is True:
            return True
        return evaluate(expr["right"], row, columns)
    left = evaluate(expr["left"], row, columns)
    right = evaluate(expr["right"], row, columns)
    if op in {"+", "-", "*", "/"}:
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
