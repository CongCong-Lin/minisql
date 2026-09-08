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
                result[member.name] = encode(getattr(self, member.name))
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
    resolved_type: str | None = field(default=None, init=False, repr=False,
                                     metadata={"serialize": False})


@dataclass
class LiteralExpr(Expr):
    """保存 Parser 转换后的值和显式类型。"""

    value: int | float | str | bool
    lit_type: str
