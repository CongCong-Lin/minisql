"""B 负责：递归下降入口，跨分段恢复由 runtime 负责。"""

from sql_compiler.ast_nodes import Stmt
from sql_compiler.errors import ParserError
from sql_compiler.lexer import Token


def parse(tokens: list[Token]) -> list[Stmt]:
    """解析含唯一结束标记的 Token 流，首个语法错误即抛出。"""
    raise NotImplementedError("M1 存根：语法分析由 B 实现")
