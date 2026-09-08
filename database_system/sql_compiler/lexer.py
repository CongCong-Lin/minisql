"""A 负责：Token 定义和词法入口。"""

from dataclasses import dataclass
from enum import Enum
from sql_compiler.errors import LexerError


class TokenType(str, Enum):
    """词法输出的六类 Token。"""

    KEYWORD = "KEYWORD"
    IDENTIFIER = "IDENTIFIER"
    CONST = "CONST"
    OPERATOR = "OPERATOR"
    DELIMITER = "DELIMITER"
    EOF = "EOF"


@dataclass
class Token:
    """保存原文与全文件字符位置。"""

    type: TokenType
    lexeme: str
    line: int
    column: int


def tokenize(text: str) -> list[Token]:
    """完整扫描；有错时抛出携带 errors 和 tokens 的 LexerError。"""
    raise NotImplementedError("M1 存根：词法分析由 A 实现")
