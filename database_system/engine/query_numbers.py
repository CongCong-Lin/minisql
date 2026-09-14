"""查询结果的宽整数与浮点运算，不改变磁盘 INT 编码。"""

import math

from sql_compiler.errors import ExecuteError

QUERY_INT_MIN = -(1 << 63)
QUERY_INT_MAX = (1 << 63) - 1


def checked_query_integer(value):
    """COUNT、SUM 及聚合后整数运算使用有符号 64 位结果。"""
    if type(value) is not int or not QUERY_INT_MIN <= value <= QUERY_INT_MAX:
        raise ExecuteError("query integer out of range (64-bit)")
    return value


def query_arithmetic(op, left, right, value_type):
    """聚合后的算术沿用整数除法向零截断，浮点结果必须有限。"""
    if op == "/" and right == 0:
        raise ExecuteError("division by zero")
    if op == "+":
        result = left + right
    elif op == "-":
        result = left - right
    elif op == "*":
        result = left * right
    elif op == "/":
        if value_type == "FLOAT":
            result = left / right
        else:
            result = abs(left) // abs(right)
            if (left < 0) != (right < 0):
                result = -result
    else:
        raise ExecuteError(f"unsupported arithmetic operator '{op}'")
    if value_type == "FLOAT":
        try:
            result = float(result)
        except OverflowError as exc:
            raise ExecuteError("query float out of range") from exc
        if not math.isfinite(result):
            raise ExecuteError("query float out of range")
        return result
    return checked_query_integer(result)
