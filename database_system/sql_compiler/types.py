"""C 负责：唯一类型规则及受检整数算术入口。"""

from sql_compiler.errors import IntegerArithmeticError

INT_MIN = -2147483648
INT_MAX = 2147483647
MAX_VARCHAR_BYTES = 255


def checked_int_arithmetic(op: str, left: int, right: int) -> int:
    """执行有符号 32 位运算，除法向零截断，失败抛内部算术异常。"""
    raise NotImplementedError("M1 存根：类型与算术规则由 C 实现")
