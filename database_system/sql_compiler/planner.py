"""D 负责：单条已检查 AST 转 JSON 兼容计划。"""

from sql_compiler.ast_nodes import Stmt
from sql_compiler.catalog import Catalog
from sql_compiler.errors import PlannerError


def plan(stmt: Stmt, catalog: Catalog) -> dict:
    """输出能被执行器单独消费的原始计划。"""
    raise NotImplementedError("M1 存根：计划生成由 D 实现")
