"""A 负责：Token 定义和词法入口。"""

from dataclasses import dataclass
from enum import Enum
import re
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
    # 词法位置按归一化后的字符流计算。BOM 是输入标记而非 SQL 字符，
    # 因而在开头直接移除，不占用一列。
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    keywords = {
        "SELECT", "FROM", "WHERE", "CREATE", "TABLE", "INSERT", "INTO",
        "VALUES", "DELETE", "UPDATE", "SET", "ORDER", "BY", "GROUP",
        "JOIN", "AND", "OR", "NOT", "NULL", "INT", "VARCHAR", "TRUE",
        "FALSE",
    }
    delimiters = {"(", ")", ",", ";"}

    tokens: list[Token] = []
    diagnostics: list[tuple[int, LexerError]] = []
    i = 0
    line = 1
    column = 1
    discovery_index = 0

    def add_error(err_line: int, err_col: int, reason: str) -> None:
        """收集单条诊断，最终再按源码位置排序。"""
        nonlocal discovery_index
        diagnostics.append((discovery_index, LexerError(err_line, err_col, reason)))
        discovery_index += 1

    def advance_one() -> str:
        """消费一个已归一化字符并更新全文件行列位置。"""
        nonlocal i, line, column
        ch = text[i]
        i += 1
        if ch == "\n":
            line += 1
            column = 1
        else:
            column += 1
        return ch

    while i < len(text):
        ch = text[i]

        # SQL 契约定义的空白是空格、制表符和归一化后的换行；
        # 其他 Unicode 控制/空白字符按非法字符处理。
        if ch in " \t\n":
            advance_one()
            continue

        start_line, start_column, start_i = line, column, i

        # SQL 行注释和块注释必须先于减号、斜杠运算符识别。
        if ch == "-" and i + 1 < len(text) and text[i + 1] == "-":
            advance_one()
            advance_one()
            while i < len(text) and text[i] != "\n":
                advance_one()
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "*":
            advance_one()
            advance_one()
            closed = False
            while i < len(text):
                if text[i] == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    advance_one()
                    advance_one()
                    closed = True
                    break
                advance_one()
            if not closed:
                add_error(start_line, start_column, "unterminated block comment")
            continue

        # 单引号字符串；连续的两个单引号代表字符串中的一个引号，
        # 但词法层保留原始词素（包括外层引号与转义）。
        if ch == "'":
            advance_one()  # opening quote
            closed = False
            while i < len(text):
                current = text[i]
                if current == "\n":
                    # 未闭合字符串跳到当前行尾，换行交由外层循环处理。
                    add_error(start_line, start_column, "unterminated string literal")
                    closed = False
                    break
                if current == "'":
                    if i + 1 < len(text) and text[i + 1] == "'":
                        advance_one()
                        advance_one()
                        continue
                    advance_one()
                    closed = True
                    break
                advance_one()
            if closed:
                tokens.append(Token(TokenType.CONST, text[start_i:i], start_line, start_column))
            elif i >= len(text) and (not text or text[-1] != "\n"):
                # 文件尾仍未遇到闭合引号；遇到换行的路径已经在上面报错。
                add_error(start_line, start_column, "unterminated string literal")
            continue

        # 标识符或保留字。先完整消费后检查长度，避免长标识符被拆成多个词。
        if ch.isascii() and (ch.isalpha() or ch == "_"):
            while i < len(text):
                current = text[i]
                if current.isascii() and (current.isalnum() or current == "_"):
                    advance_one()
                else:
                    break
            lexeme = text[start_i:i]
            if len(lexeme) > 64:
                add_error(start_line, start_column,
                          f"identifier exceeds 64 characters: '{lexeme}'")
            else:
                token_type = TokenType.KEYWORD if lexeme.upper() in keywords else TokenType.IDENTIFIER
                tokens.append(Token(token_type, lexeme, start_line, start_column))
            continue

        # 数字：从数字起点连续吸收字母、数字、下划线、小数点，
        # 再整体判断合法形式，确保 12abc/1.2.3 不会被拆分。
        if ch.isdigit() and ch.isascii():
            while i < len(text):
                current = text[i]
                if (current.isascii() and (current.isalnum() or current in "_.")):
                    advance_one()
                else:
                    break
            lexeme = text[start_i:i]
            if not (re.fullmatch(r"[0-9]+", lexeme) or
                    re.fullmatch(r"[0-9]+\.[0-9]+", lexeme)):
                add_error(start_line, start_column,
                          f"invalid numeric literal: '{lexeme}'")
            else:
                tokens.append(Token(TokenType.CONST, lexeme, start_line, start_column))
            continue

        # 以小数点开头的数值（如 .5）也是一个完整的非法数字词素，
        # 避免将其拆成非法字符和后续整数。
        if ch == "." and i + 1 < len(text) and text[i + 1].isascii() and text[i + 1].isdigit():
            advance_one()
            while i < len(text):
                current = text[i]
                if current.isascii() and (current.isalnum() or current in "_."):
                    advance_one()
                else:
                    break
            add_error(start_line, start_column,
                      f"invalid numeric literal: '{text[start_i:i]}'")
            continue

        # 最长匹配多字符运算符，并处理不支持的 ==。
        if ch == "=" and i + 1 < len(text) and text[i + 1] == "=":
            advance_one()
            advance_one()
            add_error(start_line, start_column, "operator '==' is not supported; did you mean '='?")
            continue
        if ch in "><!":
            if i + 1 < len(text) and text[i + 1] == "=":
                lexeme = ch + "="
                advance_one()
                advance_one()
                tokens.append(Token(TokenType.OPERATOR, lexeme, start_line, start_column))
            elif ch in "><":
                advance_one()
                tokens.append(Token(TokenType.OPERATOR, ch, start_line, start_column))
            else:
                # 裸 ! 没有对应 SQL 运算符。
                advance_one()
                add_error(start_line, start_column, "illegal character '!'")
            continue
        if ch in "+-*/=":
            advance_one()
            tokens.append(Token(TokenType.OPERATOR, ch, start_line, start_column))
            continue

        if ch in delimiters:
            advance_one()
            tokens.append(Token(TokenType.DELIMITER, ch, start_line, start_column))
            continue

        # 其余字符逐个报告并跳过，之后继续扫描。
        advance_one()
        add_error(start_line, start_column, f"illegal character {ch!r}")

    tokens.append(Token(TokenType.EOF, "", line, column))

    if diagnostics:
        # Python 的 sort 稳定，因此显式 discovery_index 可保证同位置保持发现顺序。
        ordered = [error for _, error in sorted(diagnostics, key=lambda item: (item[1].line,
                                                                                item[1].column,
                                                                                item[0]))]
        first = ordered[0]
        raise LexerError(first.line, first.column, first.reason,
                         errors=ordered, tokens=tokens)
    return tokens
