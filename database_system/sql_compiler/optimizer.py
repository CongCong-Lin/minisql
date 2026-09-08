"""D 负责：保持结果及错误行为的计划优化。"""

from copy import deepcopy

from sql_compiler import types as type_rules
from sql_compiler.errors import IntegerArithmeticError, PlannerError

_ARITH_OPS = {"+", "-", "*", "/"}
_CMP_OPS = {"=", "!=", ">", ">=", "<", "<="}
_CMP_FUNCS = {
    "=": lambda left, right: left == right,
    "!=": lambda left, right: left != right,
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
}


def optimize(plan: dict) -> tuple[dict, list[str]]:
    """返回独立优化计划和按首次触发顺序去重的规则名。"""
    if not isinstance(plan, dict) or "op" not in plan:
        raise PlannerError(1, 1, "optimize requires a plan dictionary")
    working = deepcopy(plan)
    log = _RuleLog()
    changed = True
    while changed:
        working, changed = _optimize_plan(working, log)
    return working, log.names


class _RuleLog:
    """记录首次触发的优化规则名。"""

    def __init__(self) -> None:
        self.names: list[str] = []
        self._seen: set[str] = set()

    def add(self, name: str) -> None:
        if name not in self._seen:
            self._seen.add(name)
            self.names.append(name)


def _optimize_plan(node: dict, log: _RuleLog) -> tuple[dict, bool]:
    """先优化子计划，再处理当前 Filter。"""
    changed = False
    result = dict(node)
    child = result.get("child")
    if isinstance(child, dict) and "op" in child:
        new_child, child_changed = _optimize_plan(child, log)
        result["child"] = new_child
        changed = changed or child_changed
    if result.get("op") == "Filter":
        new_pred, pred_changed = _optimize_expr(result["predicate"], log)
        result["predicate"] = new_pred
        changed = changed or pred_changed
        if _is_true_literal(new_pred):
            log.add("boolean_simplification")
            return result["child"], True
    return result, changed


def _optimize_expr(expr: dict, log: _RuleLog) -> tuple[dict, bool]:
    """表达式后序：先常量折叠，再布尔化简。"""
    if not isinstance(expr, dict):
        return expr, False
    kind = expr.get("node")
    if kind == "BinaryExpr":
        left, left_changed = _optimize_expr(expr["left"], log)
        right, right_changed = _optimize_expr(expr["right"], log)
        current = {**expr, "left": left, "right": right}
        folded = _fold_binary(current)
        if folded is not None:
            log.add("constant_folding")
            return folded, True
        simplified = _simplify_boolean(current, log)
        if simplified is not current:
            return simplified, True
        return current, left_changed or right_changed
    if kind == "UnaryExpr":
        operand, operand_changed = _optimize_expr(expr["operand"], log)
        current = {**expr, "operand": operand}
        simplified = _simplify_not(current, log)
        if simplified is not current:
            return simplified, True
        return current, operand_changed
    return expr, False


def _fold_binary(node: dict) -> dict | None:
    """两侧均为字面量时折叠算术或比较；除零和溢出保留原子树。"""
    op = node["op"]
    left, right = node["left"], node["right"]
    if not _is_literal(left) or not _is_literal(right):
        return None
    if op in _ARITH_OPS:
        if not _is_int_literal(left) or not _is_int_literal(right):
            return None
        try:
            value = type_rules.checked_int_arithmetic(op, left["value"], right["value"])
        except IntegerArithmeticError:
            return None
        return _literal(value, "INT", node)
    if op in _CMP_OPS:
        if _is_int_literal(left) and _is_int_literal(right):
            return _literal(_CMP_FUNCS[op](left["value"], right["value"]), "BOOL", node)
        if _is_varchar_literal(left) and _is_varchar_literal(right):
            return _literal(_CMP_FUNCS[op](left["value"], right["value"]), "BOOL", node)
    return None


def _simplify_boolean(node: dict, log: _RuleLog) -> dict:
    """按契约化简 AND/OR，无法证明安全时保留原式。"""
    op = node["op"]
    left, right = node["left"], node["right"]
    left_bool = _bool_value(left)
    right_bool = _bool_value(right)
    if op == "AND":
        if left_bool is True:
            log.add("boolean_simplification")
            return right
        if right_bool is True:
            log.add("boolean_simplification")
            return left
        if left_bool is False:
            log.add("boolean_simplification")
            return left
        if right_bool is False and not _contains_arithmetic(left):
            log.add("boolean_simplification")
            return right
    elif op == "OR":
        if left_bool is False:
            log.add("boolean_simplification")
            return right
        if right_bool is False:
            log.add("boolean_simplification")
            return left
        if left_bool is True:
            log.add("boolean_simplification")
            return left
        if right_bool is True and not _contains_arithmetic(left):
            log.add("boolean_simplification")
            return right
    return node


def _simplify_not(node: dict, log: _RuleLog) -> dict:
    """折叠常量 NOT。"""
    if node.get("op") != "NOT":
        return node
    operand_bool = _bool_value(node["operand"])
    if operand_bool is True:
        log.add("boolean_simplification")
        return _literal(False, "BOOL", node)
    if operand_bool is False:
        log.add("boolean_simplification")
        return _literal(True, "BOOL", node)
    return node


def _literal(value: object, lit_type: str, source: dict) -> dict:
    """新字面量继承被替换节点的源码位置。"""
    return {
        "node": "LiteralExpr",
        "value": value,
        "lit_type": lit_type,
        "line": source["line"],
        "column": source["column"],
    }


def _is_literal(expr: dict) -> bool:
    return isinstance(expr, dict) and expr.get("node") == "LiteralExpr"


def _is_int_literal(expr: dict) -> bool:
    return (
        _is_literal(expr)
        and expr.get("lit_type") == "INT"
        and type(expr.get("value")) is int
    )


def _is_varchar_literal(expr: dict) -> bool:
    return (
        _is_literal(expr)
        and expr.get("lit_type") == "VARCHAR"
        and isinstance(expr.get("value"), str)
    )


def _is_true_literal(expr: dict) -> bool:
    return _bool_value(expr) is True


def _bool_value(expr: dict) -> bool | None:
    if (
        _is_literal(expr)
        and expr.get("lit_type") == "BOOL"
        and type(expr.get("value")) is bool
    ):
        return expr["value"]
    return None


def _contains_arithmetic(expr: object) -> bool:
    """子树中是否仍有可能在求值时溢出或除零的算术节点。"""
    if not isinstance(expr, dict):
        return False
    if expr.get("node") == "BinaryExpr" and expr.get("op") in _ARITH_OPS:
        return True
    return any(_contains_arithmetic(value) for value in expr.values())
