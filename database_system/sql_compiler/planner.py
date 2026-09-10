"""D 负责：单条已检查 AST 转 JSON 兼容计划。"""

from sql_compiler.ast_nodes import (
    CreateTableStmt, DeleteStmt, InsertStmt, SelectStmt, Stmt, UpdateStmt,
)
from sql_compiler.catalog import Catalog
from sql_compiler.errors import PlannerError


def plan(stmt: Stmt, catalog: Catalog) -> dict:
    """输出能被执行器单独消费的原始计划。"""
    del catalog  # 表存在性与列绑定已由语义阶段完成
    if isinstance(stmt, CreateTableStmt):
        return {
            "op": "CreateTable",
            "table": stmt.table,
            "columns": [
                {
                    "name": column.name,
                    "type": column.col_type,
                    "line": column.line,
                    "column": column.column,
                }
                for column in stmt.columns
            ],
        }
    if isinstance(stmt, InsertStmt):
        return {
            "op": "Insert",
            "table": stmt.table,
            "columns": list(stmt.columns),
            "values": [literal.value for literal in stmt.values],
        }
    if isinstance(stmt, SelectStmt):
        if stmt.binding is not None:
            from sql_compiler.query_binding import query_plan
            return query_plan(stmt.binding)
        from sql_compiler.query_binding import is_extended_query
        if is_extended_query(stmt):
            raise PlannerError(stmt.line, stmt.column, "extended query requires semantic binding")
        return {
            "op": "Project",
            "columns": stmt.columns if stmt.columns == "*" else list(stmt.columns),
            "child": _scan_with_filter(stmt.table, stmt.where),
        }
    if isinstance(stmt, DeleteStmt):
        return {
            "op": "Delete",
            "table": stmt.table,
            "child": _scan_with_filter(stmt.table, stmt.where),
        }
    if isinstance(stmt, UpdateStmt):
        from copy import deepcopy
        if stmt.binding is None:
            raise PlannerError(stmt.line, stmt.column, "UPDATE requires semantic binding")
        binding = stmt.binding
        child = {"op": "SeqScan", "table": binding["table"]}
        if binding["where"] is not None:
            child = {"op": "Filter", "predicate": deepcopy(binding["where"]), "child": child}
        return {"op": "Update", "table": binding["table"],
                "assignments": deepcopy(binding["assignments"]), "child": child}
    raise PlannerError(
        stmt.line, stmt.column,
        f"unsupported statement: {type(stmt).__name__}",
    )


def _scan_with_filter(table: str, where) -> dict:
    """构造 SeqScan，存在 WHERE 时外包 Filter。"""
    scan = {"op": "SeqScan", "table": table}
    if where is None:
        return scan
    return {"op": "Filter", "predicate": where.to_dict(), "child": scan}
