"""C 负责：语义只检查及标注，不提交目录，不执行表达式。"""

from sql_compiler.ast_nodes import (
    BinaryExpr, CreateTableStmt, DeleteStmt, Expr, IdentifierExpr, InsertStmt,
    LiteralExpr, SelectStmt, Stmt, UnaryExpr, UpdateStmt,
)
from sql_compiler.catalog import Catalog
from sql_compiler.errors import SemanticError
from sql_compiler.types import (
    MAX_VARCHAR_BYTES, expression_type, insert_type_matches, is_boolean,
)


def _error(node, reason: str) -> SemanticError:
    """节点行列已经是全文件位置，不重新计算。"""
    return SemanticError(node.line, node.column, reason)


def _literal_errors(expr: LiteralExpr) -> list[SemanticError]:
    """所有字符串字面量都受 UTF-8 字节上限约束。"""
    if expr.lit_type == "VARCHAR":
        try:
            size = len(expr.value.encode("utf-8"))
        except (AttributeError, UnicodeError) as exc:
            raise _error(expr, "字符串字面量不是有效 UTF-8 文本") from exc
        if size > MAX_VARCHAR_BYTES:
            return [_error(expr, "varchar literal exceeds 255 bytes")]
    return []


def _expression(expr: Expr, table: str, catalog: Catalog) -> str:
    """用显式栈后序推导类型，长表达式不占用 Python 递归栈。"""
    pending = [(expr, False)]
    while pending:
        current, children_ready = pending.pop()
        if not children_ready:
            if isinstance(current, BinaryExpr):
                # 栈后进先出：先压右侧，实际仍按左、右、父节点检查。
                pending.extend(((current, True), (current.right, False),
                                (current.left, False)))
                continue
            if isinstance(current, UnaryExpr):
                pending.extend(((current, True), (current.operand, False)))
                continue

        if isinstance(current, LiteralExpr):
            errors = _literal_errors(current)
            if errors:
                raise errors[0]
            if current.lit_type not in {"INT", "VARCHAR", "FLOAT", "BOOL"}:
                raise _error(current, f"未知字面量类型：{current.lit_type}")
            result = current.lit_type
        elif isinstance(current, IdentifierExpr):
            if current.qualifier is not None and current.qualifier.lower() != table.lower():
                raise _error(current, f"列不存在：{current.qualifier}.{current.name}")
            result = catalog.get_type(table, current.name)
            if result is None:
                raise _error(current, f"列不存在：{current.name}")
            current.resolved_type = result
        elif isinstance(current, BinaryExpr):
            # 子节点已经在本次遍历中校验并标注，不使用上次分析的缓存。
            left, right = current.left.expr_type, current.right.expr_type
            result = expression_type(current.op, left, right)
            if result is None:
                raise _error(current, f"运算符 {current.op} 不支持类型 {left} 和 {right}")
        elif isinstance(current, UnaryExpr):
            operand = current.operand.expr_type
            result = expression_type(current.op, operand)
            if result is None:
                raise _error(current, f"运算符 {current.op} 不支持类型 {operand}")
        else:
            raise _error(current, "未知表达式节点")
        current.expr_type = result
    return expr.expr_type


def analyze(stmts: list[Stmt], catalog: Catalog) -> list[Stmt]:
    """基于同一个目录逐句检查；顺序建表可见性由运行时编排。"""
    for stmt in stmts:
        if isinstance(stmt, UpdateStmt):
            from sql_compiler.query_binding import bind_update
            stmt.binding = None
            stmt.binding = bind_update(stmt, catalog)
            continue
        if isinstance(stmt, SelectStmt):
            from sql_compiler.query_binding import bind_query, is_extended_query
            stmt.binding = None
            if is_extended_query(stmt):
                stmt.binding = bind_query(stmt, catalog)
                continue
        if not isinstance(stmt, (CreateTableStmt, InsertStmt, SelectStmt, DeleteStmt)):
            raise _error(stmt, "不支持的语句节点")
        if stmt.table.lower() == "__catalog__":
            raise _error(stmt, "用户语句不得创建或访问系统目录")
        if isinstance(stmt, CreateTableStmt):
            if catalog.find_table(stmt.table) is not None:
                raise _error(stmt, f"表已存在：{stmt.table}")
            # 共用 C 自身的逻辑校验，不通过建表或快照产生隐式登记。
            catalog._validate(stmt.table, stmt.columns, locate_columns=True)
            continue

        schema = catalog.find_table(stmt.table)
        if schema is None:
            raise _error(stmt, f"表不存在：{stmt.table}")
        known = {col.name.lower(): col.col_type for col in schema["columns"]}
        if isinstance(stmt, InsertStmt):
            if len(stmt.columns) != len(stmt.values):
                raise _error(stmt, "INSERT 目标列数与值数不一致")
            seen = set()
            for name in stmt.columns:
                key = name.lower()
                if key in seen:
                    raise _error(stmt, f"INSERT 目标列重复：{name}")
                if key not in known:
                    raise _error(stmt, f"列不存在：{name}")
                seen.add(key)
            if seen != set(known):
                raise _error(stmt, "INSERT 目标列必须完整覆盖表列")
            errors = []
            for name, value in zip(stmt.columns, stmt.values):
                wanted = known[name.lower()]
                if not insert_type_matches(wanted, value.lit_type):
                    errors.append(_error(value, f"列 {name} 需要 {wanted}，实际为 {value.lit_type}"))
                errors.extend(_literal_errors(value))
                value.expr_type = value.lit_type
            if errors:
                errors.sort(key=lambda err: (err.line, err.column))
                first = errors[0]
                if len(errors) == 1:
                    raise first
                raise SemanticError(first.line, first.column, first.reason, errors=errors)
        else:
            if isinstance(stmt, SelectStmt) and stmt.columns != "*":
                for name in stmt.columns:
                    if name.lower() not in known:
                        raise _error(stmt, f"列不存在：{name}")
            if stmt.where is not None:
                result = _expression(stmt.where, stmt.table, catalog)
                if not is_boolean(result):
                    raise _error(stmt.where, "WHERE 条件必须为 BOOL")
    return stmts
