"""C 负责：只检查和标注，不提交目录。"""

from sql_compiler.ast_nodes import Stmt
from sql_compiler.catalog import Catalog
from sql_compiler.errors import SemanticError


def analyze(stmts: list[Stmt], catalog: Catalog) -> list[Stmt]:
    """检查当前目录中的语句，成功时原地标注并返回原列表。"""
    raise NotImplementedError("M1 存根：语义分析由 C 实现")
