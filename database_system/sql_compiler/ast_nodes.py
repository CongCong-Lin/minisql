"""B 负责：公共 AST 结构与结构性序列化。"""

from __future__ import annotations
from dataclasses import dataclass, field, fields
from typing import Literal


@dataclass(kw_only=True)
class Node:
    """所有节点的位置使用关键字参数。"""

    line: int
    column: int

    def to_dict(self) -> dict:
        """输出节点结构快照，排除语义分析附加属性。"""
        def encode(value):
            if isinstance(value, Node):
                return value.to_dict()
            if isinstance(value, list):
                return [encode(item) for item in value]
            return value

        result = {"node": type(self).__name__}
        for member in fields(self):
            if member.metadata.get("serialize", True):
                value = getattr(self, member.name)
                if member.metadata.get("omit_default") and value == member.default:
                    continue
                if member.metadata.get("omit_empty") and (value is None or value == []):
                    continue
                result[member.name] = encode(value)
        return result


@dataclass
class Stmt(Node):
    """语句节点的公共基类。"""


@dataclass
class Expr(Node):
    """表达式节点的公共基类。"""

    expr_type: str | None = field(default=None, init=False, repr=False,
                                  metadata={"serialize": False})


@dataclass
class ColumnDef(Node):
    """保存原始列名、列类型及定义位置。"""

    name: str
    col_type: str
    nullable: bool = field(default=True, kw_only=True, metadata={"omit_default": True})


@dataclass
class CreateTableStmt(Stmt):
    """建表语句。"""

    table: str
    columns: list[ColumnDef]


@dataclass
class InsertStmt(Stmt):
    """保存 SQL 中的目标列与值顺序。"""

    table: str
    columns: list[str]
    values: list[LiteralExpr]


@dataclass
class SelectStmt(Stmt):
    """查询语句，星号使用字符串。"""

    columns: list[str] | Literal["*"]
    table: str
    where: Expr | None
    items: list[SelectItem] = field(default_factory=list, kw_only=True,
                                    metadata={"omit_empty": True})
    alias: str | None = field(default=None, kw_only=True, metadata={"omit_empty": True})
    joins: list[JoinClause] = field(default_factory=list, kw_only=True,
                                   metadata={"omit_empty": True})
    order_by: list[OrderItem] = field(default_factory=list, kw_only=True,
                                     metadata={"omit_empty": True})
    group_by: list[IdentifierExpr] = field(default_factory=list, kw_only=True,
                                          metadata={"omit_empty": True})
    having: Expr | None = field(default=None, kw_only=True, metadata={"omit_empty": True})
    binding: dict | None = field(default=None, init=False, repr=False,
                                 metadata={"serialize": False})
    selection_items: list[SelectItem] = field(default_factory=list, init=False, repr=False,
                                             metadata={"serialize": False})


@dataclass
class DeleteStmt(Stmt):
    """删除语句。"""

    table: str
    where: Expr | None


@dataclass
class BinaryExpr(Expr):
    """二元运算的位置取运算符。"""

    op: str
    left: Expr
    right: Expr


@dataclass
class UnaryExpr(Expr):
    """一元 NOT 表达式。"""

    op: str
    operand: Expr


@dataclass
class IdentifierExpr(Expr):
    """标识符表达式，保留原始大小写。"""

    name: str
    qualifier: str | None = field(default=None, kw_only=True, metadata={"omit_empty": True})
    resolved_type: str | None = field(default=None, init=False, repr=False,
                                     metadata={"serialize": False})


@dataclass
class LiteralExpr(Expr):
    """保存 Parser 转换后的值和显式类型。"""

    value: int | float | str | bool | None
    lit_type: str


@dataclass
class ControlStmt(Stmt):
    """事务、索引、解释及授权语句的明确控制节点。"""

    action: str
    name: str | None = None
    table: str | None = None
    column_name: str | None = None
    readonly: bool = False
    permissions: list[str] = field(default_factory=list)
    subject: str | None = None
    statement: Stmt | None = None
    format: str = "TEXT"


@dataclass
class SelectItem(Node):
    """选择项；expr 为空表示星号，qualifier 用于限定星号。"""

    expr: Expr | None
    alias: str | None = None
    qualifier: str | None = None


@dataclass
class OrderItem(Node):
    """排序键和方向，未指定方向时升序。"""

    expr: Expr
    descending: bool = False


@dataclass
class JoinClause(Node):
    """内连接右表、可选别名及 ON 条件。"""

    table: str
    alias: str | None
    on: Expr


@dataclass
class AggregateExpr(Expr):
    """聚合参数仅允许列引用，空参数表示 COUNT(*)。"""

    function: str
    argument: IdentifierExpr | None


@dataclass
class Assignment(Node):
    """更新目标列及基于原行求值的表达式。"""

    name: str
    value: Expr


@dataclass
class UpdateStmt(Stmt):
    """单表多列更新，where 为空时更新所有行。"""

    table: str
    assignments: list[Assignment]
    where: Expr | None
    binding: dict | None = field(default=None, init=False, repr=False,
                                 metadata={"serialize": False})
