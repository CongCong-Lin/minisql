"""D 负责：表达式求值及短路行为。"""

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError


def evaluate(expr: dict, row: tuple, columns: list[ColumnDef]) -> object:
    """依据列序求值，不访问 Catalog 或存储。"""
    raise NotImplementedError("M1 存根：表达式求值由 D 实现")
