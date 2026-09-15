"""B 负责：Parser 和 AST 行为测试。"""

import pytest

from sql_compiler.ast_nodes import BinaryExpr, LiteralExpr
from sql_compiler.errors import ParserError
from sql_compiler.lexer import Token, TokenType
from sql_compiler.parser import parse


def _token(kind, lexeme, column):
    return Token(kind, lexeme, 1, column)


def _kw(value, column):
    return _token(TokenType.KEYWORD, value, column)


def _id(value, column):
    return _token(TokenType.IDENTIFIER, value, column)


def _op(value, column):
    return _token(TokenType.OPERATOR, value, column)


def _delim(value, column):
    return _token(TokenType.DELIMITER, value, column)


def _const(value, column):
    return _token(TokenType.CONST, value, column)


def _eof(column):
    return _token(TokenType.EOF, "", column)


def test_parser_builds_required_precedence_tree():
    tokens = [
        _kw("SELECT", 1), _id("a", 8), _kw("FROM", 10), _id("t", 15),
        _kw("WHERE", 17), _id("a", 23), _op("=", 24), _const("1", 25),
        _kw("OR", 27), _id("b", 30), _op("=", 31), _const("2", 32),
        _kw("AND", 34), _id("c", 38), _op("=", 39), _const("3", 40),
        _delim(";", 41), _eof(42),
    ]

    where = parse(tokens)[0].where
    assert isinstance(where, BinaryExpr)
    assert where.op == "OR"
    assert where.right.op == "AND"


def test_parser_converts_literals_and_preserves_positions():
    tokens = [
        _kw("INSERT", 1), _kw("INTO", 8), _id("t", 13), _delim("(", 14),
        _id("id", 15), _delim(",", 17), _id("name", 19), _delim(")", 23),
        _kw("VALUES", 25), _delim("(", 31),
    ]
    tokens.extend([_const("1", 32), _delim(",", 33), _const("'A''B'", 35),
                   _delim(")", 42), _delim(";", 43), _eof(44)])

    stmt = parse(tokens)[0]
    assert stmt.values[0] == LiteralExpr(1, "INT", line=1, column=32)
    assert stmt.values[1] == LiteralExpr("A'B", "VARCHAR", line=1, column=35)


def test_parser_accepts_null_and_rejects_out_of_range_integer():
    null_tokens = [
        _kw("SELECT", 1), _op("*", 8), _kw("FROM", 10), _id("t", 15),
        _kw("WHERE", 17), _kw("NULL", 23), _delim(";", 27), _eof(28),
    ]
    assert parse(null_tokens)[0].where.value is None

    integer_tokens = [
        _kw("INSERT", 1), _kw("INTO", 8), _id("t", 13), _delim("(", 14),
        _id("id", 15), _delim(")", 17), _kw("VALUES", 19),
        _delim("(", 25), _const("2147483648", 26), _delim(")", 36),
        _delim(";", 37), _eof(38),
    ]
    with pytest.raises(ParserError, match="integer literal out of range"):
        parse(integer_tokens)


def test_parser_requires_statement_terminator():
    tokens = [
        _kw("SELECT", 1), _op("*", 8), _kw("FROM", 10), _id("t", 15),
        _eof(16),
    ]
    with pytest.raises(ParserError, match="expected: ';'"):
        parse(tokens)
