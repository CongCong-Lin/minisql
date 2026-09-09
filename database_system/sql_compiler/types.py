"""C 负责：唯一类型规则及受检整数算术入口。"""

from sql_compiler.errors import IntegerArithmeticError

INT_MIN = -2147483648
INT_MAX = 2147483647
MAX_VARCHAR_BYTES = 255


def expression_type(op: str, left: str, right: str | None = None) -> str | None:
    """返回运算结果类型；不支持的组合返回空值，由语义层定位。"""
    if op == "NOT":
        return "BOOL" if left == "BOOL" and right is None else None
    if op in {"+", "-", "*", "/"} and left == right == "INT":
        return "INT"
    if op in {"=", "!=", ">", ">=", "<", "<="}:
        return "BOOL" if left == right and left in {"INT", "VARCHAR"} else None
    if op in {"AND", "OR"} and left == right == "BOOL":
        return "BOOL"
    return None


def insert_type_matches(target: str, value_type: str) -> bool:
    """插入仅允许两种存储类型的精确匹配。"""
    return target in {"INT", "VARCHAR"} and target == value_type


def is_boolean(type_name: str) -> bool:
    """WHERE 必须具有显式布尔类型。"""
    return type_name == "BOOL"


def checked_int_arithmetic(op: str, left: int, right: int) -> int:
    """执行有符号 32 位运算，除法向零截断，失败抛内部算术异常。"""
    if type(left) is not int or type(right) is not int:
        raise IntegerArithmeticError("整数运算的操作数必须为 INT")
    if not (INT_MIN <= left <= INT_MAX and INT_MIN <= right <= INT_MAX):
        raise IntegerArithmeticError("integer arithmetic out of range")
    if op == "+":
        result = left + right
    elif op == "-":
        result = left - right
    elif op == "*":
        result = left * right
    elif op == "/":
        if right == 0:
            raise IntegerArithmeticError("division by zero")
        result = abs(left) // abs(right)
        if (left < 0) != (right < 0):
            result = -result
    else:
        raise IntegerArithmeticError(f"不支持的整数运算符：{op}")
    if not INT_MIN <= result <= INT_MAX:
        raise IntegerArithmeticError("integer arithmetic out of range")
    return result
