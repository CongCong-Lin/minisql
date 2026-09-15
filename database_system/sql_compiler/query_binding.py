"""扩展查询的名字解析与类型检查，不访问记录或提交目录。"""

from __future__ import annotations

from copy import deepcopy

from sql_compiler.ast_nodes import (
    AggregateExpr, BinaryExpr, IdentifierExpr, LiteralExpr, SelectItem, SelectStmt, UnaryExpr,
)
from sql_compiler.errors import SemanticError
from sql_compiler.types import expression_type, MAX_VARCHAR_BYTES, insert_type_matches

_NUMERIC = {"INT", "BIGINT", "FLOAT"}
_COMPARISONS = {"=", "!=", "<", "<=", ">", ">="}


def _error(node, reason):
    return SemanticError(node.line, node.column, reason)


def _walk(expr):
    """迭代遍历表达式，供扩展检测和聚合检测使用。"""
    pending = [expr] if expr is not None else []
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, BinaryExpr):
            pending.extend((node.right, node.left))
        elif isinstance(node, UnaryExpr):
            pending.append(node.operand)


def is_extended_query(stmt: SelectStmt) -> bool:
    """识别需要查询作用域的语法，基础语句保留原有输出形式。"""
    if stmt.items or stmt.alias or stmt.joins or stmt.order_by or stmt.group_by or stmt.having:
        return True
    return any(isinstance(node, AggregateExpr)
               or isinstance(node, IdentifierExpr) and node.qualifier is not None
               for node in _walk(stmt.where))


def bind_query(stmt: SelectStmt, catalog) -> dict:
    """给扩展查询生成绑定信息，计划生成器随后消费该信息。"""
    return _Binder(stmt, catalog).bind()


def bind_update(stmt, catalog) -> dict:
    """赋值类型必须与磁盘列一致，表达式全部绑定到原行位置。"""
    binder = _Binder(stmt, catalog)
    source = binder._source(stmt.table, None, stmt)
    known = {column["name"].lower(): (index, column["type"])
             for index, column in enumerate(binder.columns)}
    seen = set()
    for assignment in stmt.assignments:
        key = assignment.name.lower()
        if key in seen:
            raise _error(assignment, f"UPDATE 目标列重复：{assignment.name}")
        if key not in known:
            raise _error(assignment, f"列不存在：{assignment.name}")
        seen.add(key)
    assignments, errors = [], []
    for assignment in stmt.assignments:
        index, wanted = known[assignment.name.lower()]
        try:
            value = binder.expression(assignment.value)
            if not insert_type_matches(wanted, value["value_type"]):
                raise _error(assignment.value,
                             f"列 {assignment.name} 需要 {wanted}，实际为 {value['value_type']}")
            assignments.append({"index": index, "expr": value})
        except SemanticError as exc:
            errors.append(exc)
    if errors:
        errors.sort(key=lambda error: (error.line, error.column))
        first = errors[0]
        raise SemanticError(first.line, first.column, first.reason,
                            errors=errors if len(errors) > 1 else None)
    return {"table": source["table"], "assignments": assignments,
            "where": binder._predicate(stmt.where)}


class _Binder:
    def __init__(self, stmt, catalog):
        self.stmt = stmt
        self.catalog = catalog
        self.sources = []
        self.columns = []
        self.group_keys = []
        self.group_positions = {}
        self.aggregates = []
        self.aggregate_positions = {}
        self.aliases = {}

    def _source(self, table, alias, node):
        if table.lower() == "__catalog__":
            raise _error(node, "用户语句不得访问系统目录")
        schema = self.catalog.find_table(table)
        if schema is None:
            raise _error(node, f"表不存在：{table}")
        qualifier = alias or schema["name"]
        if any(source["qualifier"].lower() == qualifier.lower() for source in self.sources):
            raise _error(node, f"表名或别名重复：{qualifier}")
        columns = [{"name": column.name, "type": column.col_type, "qualifier": qualifier}
                   for column in schema["columns"]]
        source = {"table": schema["name"], "qualifier": qualifier, "columns": columns}
        self.sources.append(source)
        self.columns.extend(columns)
        return source

    def _matches(self, expr):
        return [(index, column) for index, column in enumerate(self.columns)
                if column["name"].lower() == expr.name.lower()
                and (expr.qualifier is None
                     or column["qualifier"].lower() == expr.qualifier.lower())]

    def _source_column(self, expr):
        matches = self._matches(expr)
        name = f"{expr.qualifier}.{expr.name}" if expr.qualifier else expr.name
        if not matches:
            raise _error(expr, f"列不存在：{name}")
        if len(matches) != 1:
            raise _error(expr, f"列名存在歧义，请指定表名或别名：{name}")
        index, column = matches[0]
        expr.resolved_type = expr.expr_type = column["type"]
        return self._bound(index, column["type"], expr)

    @staticmethod
    def _bound(index, value_type, node):
        return {"node": "BoundColumnExpr", "index": index, "value_type": value_type,
                "line": node.line, "column": node.column}

    def _column(self, expr, grouped, aliases):
        if aliases and expr.qualifier is None and expr.name.lower() in self.aliases:
            if self._matches(expr):
                raise _error(expr, f"结果别名与来源列存在歧义：{expr.name}")
            bound = deepcopy(self.aliases[expr.name.lower()])
            expr.resolved_type = expr.expr_type = bound["value_type"]
            return bound
        bound = self._source_column(expr)
        if grouped:
            if bound["index"] not in self.group_positions:
                raise _error(expr, f"非聚合列必须出现在 GROUP BY 中：{expr.name}")
            bound["index"] = self.group_positions[bound["index"]]
        return bound

    def _aggregate(self, expr, grouped):
        if not grouped:
            raise _error(expr, "WHERE、ON 和更新表达式中不能使用聚合函数")
        function = expr.function
        if function not in {"COUNT", "SUM", "AVG", "MIN", "MAX"}:
            raise _error(expr, f"不支持的聚合函数：{function}")
        argument = self._source_column(expr.argument) if expr.argument is not None else None
        if argument is None and function != "COUNT":
            raise _error(expr, "只有 COUNT 可以使用星号参数")
        arg_type = argument["value_type"] if argument else None
        if function in {"SUM", "AVG"} and arg_type not in {"INT", "FLOAT"}:
            raise _error(expr, f"{function} 只接受 INT 或 FLOAT 列")
        value_type = ("FLOAT" if function == "SUM" and arg_type == "FLOAT" else "BIGINT" if function in {"COUNT", "SUM"}
                      else "FLOAT" if function == "AVG" else arg_type)
        key = (function, argument["index"] if argument else None)
        if key not in self.aggregate_positions:
            self.aggregate_positions[key] = len(self.aggregates)
            self.aggregates.append({"function": function, "argument": argument,
                                    "value_type": value_type})
        expr.expr_type = value_type
        return self._bound(len(self.group_keys) + self.aggregate_positions[key], value_type, expr)

    def expression(self, expr, *, grouped=False, aliases=False):
        """绑定后的表达式显式带结果类型，运行时无需再次按名称查找。"""
        built = {}
        pending = [(expr, False)]
        while pending:
            node, ready = pending.pop()
            if not ready and isinstance(node, BinaryExpr):
                pending.extend(((node, True), (node.right, False), (node.left, False)))
                continue
            if not ready and isinstance(node, UnaryExpr):
                pending.extend(((node, True), (node.operand, False)))
                continue
            if isinstance(node, IdentifierExpr):
                result = self._column(node, grouped, aliases)
            elif isinstance(node, AggregateExpr):
                result = self._aggregate(node, grouped)
            elif isinstance(node, LiteralExpr):
                if node.lit_type == "VARCHAR":
                    try:
                        size = len(node.value.encode("utf-8"))
                    except (AttributeError, UnicodeError) as exc:
                        raise _error(node, "字符串字面量不是有效 UTF-8 文本") from exc
                    if size > MAX_VARCHAR_BYTES:
                        raise _error(node, "varchar literal exceeds 255 bytes")
                result = {**node.to_dict(), "value_type": node.lit_type}
            elif isinstance(node, (BinaryExpr, UnaryExpr)):
                left_node = node.left if isinstance(node, BinaryExpr) else node.operand
                left = built[id(left_node)]
                right = built[id(node.right)] if isinstance(node, BinaryExpr) else None
                left_type, right_type = left["value_type"], right["value_type"] if right else None
                value_type = expression_type(node.op, left_type, right_type)
                if grouped and left_type in _NUMERIC and right_type in _NUMERIC:
                    if node.op in _COMPARISONS:
                        value_type = "BOOL"
                    elif node.op in {"+", "-", "*", "/"} and "BIGINT" in {left_type, right_type}:
                        # 只有聚合结果这类宽整数才走 64 位；普通 INT 表达式保持 32 位受检运算。
                        value_type = "FLOAT" if "FLOAT" in {left_type, right_type} else "BIGINT"
                if value_type is None:
                    raise _error(node, f"运算符 {node.op} 不支持类型 {left_type} 和 {right_type}")
                result = {"node": type(node).__name__, "op": node.op,
                          "line": node.line, "column": node.column, "value_type": value_type}
                if right is None:
                    result["operand"] = left
                else:
                    result.update(left=left, right=right)
            else:
                raise _error(node, "未知表达式节点")
            node.expr_type = result["value_type"]
            built[id(node)] = result
        return built[id(expr)]

    def _predicate(self, expr, *, grouped=False, aliases=False, label="WHERE"):
        if expr is None:
            return None
        bound = self.expression(expr, grouped=grouped, aliases=aliases)
        if bound["value_type"] not in {"BOOL", "NULL"}:
            raise _error(expr, f"{label} 条件必须为 BOOL")
        return bound

    def _items(self):
        stmt = self.stmt
        if stmt.selection_items or stmt.items:
            return stmt.selection_items or stmt.items
        if stmt.columns == "*":
            return [SelectItem(None, line=stmt.line, column=stmt.column)]
        return [SelectItem(IdentifierExpr(name, line=stmt.line, column=stmt.column),
                           line=stmt.line, column=stmt.column) for name in stmt.columns]

    def _expand_items(self, items):
        expanded = []
        for item in items:
            if item.expr is not None:
                expanded.append(item)
                continue
            matching = [column for column in self.columns
                        if item.qualifier is None
                        or column["qualifier"].lower() == item.qualifier.lower()]
            if not matching:
                raise _error(item, f"表名或别名不存在：{item.qualifier}")
            for column in matching:
                expr = IdentifierExpr(column["name"], qualifier=column["qualifier"],
                                      line=item.line, column=item.column)
                expanded.append(SelectItem(expr, line=item.line, column=item.column))
        return expanded

    def _label(self, expr):
        if isinstance(expr, IdentifierExpr):
            source = self._source_column(expr)
            column = self.columns[source["index"]]
            return (f"{column['qualifier']}.{column['name']}" if self.stmt.joins else column["name"])
        argument = "*"
        if expr.argument is not None:
            argument = expr.argument.name
            if expr.argument.qualifier:
                argument = f"{expr.argument.qualifier}.{argument}"
        return f"{expr.function}({argument})"

    def bind(self):
        stmt = self.stmt
        self._source(stmt.table, stmt.alias, stmt)
        joins = []
        for join in stmt.joins:
            source = self._source(join.table, join.alias, join)
            joins.append({"source": source, "on": self._predicate(join.on, label="ON")})
        where = self._predicate(stmt.where)
        items = self._expand_items(self._items())
        expressions = [item.expr for item in items] + [stmt.having] + [item.expr for item in stmt.order_by]
        grouped = bool(stmt.group_by) or any(isinstance(node, AggregateExpr)
                                             for expr in expressions for node in _walk(expr))
        for expr in stmt.group_by:
            bound = self._source_column(expr)
            if bound["index"] not in self.group_positions:
                self.group_positions[bound["index"]] = len(self.group_keys)
                self.group_keys.append(bound)
        projected = []
        for item in items:
            bound = self.expression(item.expr, grouped=grouped)
            label = item.alias or self._label(item.expr)
            if item.alias is not None:
                key = item.alias.lower()
                if key in self.aliases:
                    raise _error(item, f"结果别名重复：{item.alias}")
                self.aliases[key] = bound
            projected.append({"expr": bound, "label": label})
        if stmt.having is not None and not grouped:
            raise _error(stmt.having, "HAVING 必须用于分组或聚合查询")
        having = self._predicate(stmt.having, grouped=grouped, aliases=True, label="HAVING")
        ordering = [{"expr": self.expression(item.expr, grouped=grouped, aliases=True),
                     "descending": item.descending} for item in stmt.order_by]
        return {"sources": self.sources, "joins": joins, "where": where, "items": projected,
                "grouped": grouped, "group_keys": self.group_keys, "aggregates": self.aggregates,
                "having": having, "order_by": ordering}


def query_plan(binding):
    """将绑定信息转换为明确的关系算子，不包含 Catalog 对象。"""
    def scan(source):
        return {"op": "SeqScan", "table": source["table"], "output": deepcopy(source["columns"])}

    node = scan(binding["sources"][0])
    for join in binding["joins"]:
        node = {"op": "NestedLoopJoin", "left": node, "right": scan(join["source"]),
                "predicate": deepcopy(join["on"])}
    if binding["where"] is not None:
        node = {"op": "Filter", "predicate": deepcopy(binding["where"]), "child": node}
    if binding["grouped"]:
        node = {"op": "Aggregate", "keys": deepcopy(binding["group_keys"]),
                "aggregates": deepcopy(binding["aggregates"]), "child": node}
    if binding["having"] is not None:
        node = {"op": "Filter", "predicate": deepcopy(binding["having"]), "child": node}
    if binding["order_by"]:
        node = {"op": "Sort", "keys": deepcopy(binding["order_by"]), "child": node}
    return {"op": "Project", "items": deepcopy(binding["items"]), "child": node}
