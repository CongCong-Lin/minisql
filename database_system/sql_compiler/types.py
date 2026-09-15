"""C 负责：唯一类型规则及受检整数算术入口。"""

from sql_compiler.errors import IntegerArithmeticError

INT_MIN = -2147483648
INT_MAX = 2147483647
MAX_VARCHAR_BYTES = 255
STORAGE_TYPES = {"INT", "VARCHAR", "FLOAT", "BOOL", "DATE"}


def expression_type(op: str, left: str, right: str | None = None) -> str | None:
    """返回运算结果类型；不支持的组合返回空值，由语义层定位。"""
    if op in {"IS NULL", "IS NOT NULL"}:
        return "BOOL" if right is None else None
    if op == "NOT":
        return "BOOL" if left in {"BOOL", "NULL"} and right is None else None
    if op in {"AND", "OR"}:
        return "BOOL" if left in {"BOOL", "NULL"} and right in {"BOOL", "NULL"} else None
    if left == "NULL":
        left = right if right != "NULL" else "INT"
    if right == "NULL":
        right = left
    if op in {"+", "-", "*", "/"} and left in {"INT", "FLOAT"} and right in {"INT", "FLOAT"}:
        return "FLOAT" if "FLOAT" in {left, right} else "INT"
    if op in {"=", "!=", ">", ">=", "<", "<="}:
        if left in {"INT", "FLOAT"} and right in {"INT", "FLOAT"}:
            return "BOOL"
        return "BOOL" if left == right and (left in {"VARCHAR", "DATE"} or left == "BOOL" and op in {"=", "!="}) else None
    return None


def insert_type_matches(target: str, value_type: str) -> bool:
    """存储类型精确匹配，另允许空值和整数提升为浮点。"""
    return target in STORAGE_TYPES and (value_type == "NULL" or target == value_type
                                        or target == "FLOAT" and value_type == "INT")


def is_boolean(type_name: str) -> bool:
    """WHERE 必须具有显式布尔类型。"""
    return type_name in {"BOOL", "NULL"}


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
