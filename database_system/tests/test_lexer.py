"""Lexer 的契约测试：正常 Token、位置及可恢复词法错误。"""

import pytest

from sql_compiler.errors import LexerError
from sql_compiler.lexer import TokenType, tokenize


def test_keywords_identifiers_numbers_and_positions():
    tokens = tokenize("\ufeffSeLeCt id_1, 12, 1.5\r\nFROM t;")
    assert [(t.type, t.lexeme) for t in tokens] == [
        (TokenType.KEYWORD, "SeLeCt"),
        (TokenType.IDENTIFIER, "id_1"),
        (TokenType.DELIMITER, ","),
        (TokenType.CONST, "12"),
        (TokenType.DELIMITER, ","),
        (TokenType.CONST, "1.5"),
        (TokenType.KEYWORD, "FROM"),
        (TokenType.IDENTIFIER, "t"),
        (TokenType.DELIMITER, ";"),
        (TokenType.EOF, ""),
    ]
    assert (tokens[0].line, tokens[0].column) == (1, 1)
    assert (tokens[-1].line, tokens[-1].column) == (2, 8)


def test_comments_and_string_escaping_do_not_emit_inner_semicolons():
    tokens = tokenize("SELECT 'It''s; ok' /* ; */ FROM t; -- ;\n")
    assert [t.lexeme for t in tokens] == [
        "SELECT", "'It''s; ok'", "FROM", "t", ";", ""
    ]


@pytest.mark.parametrize("text", ["12abc", "1.2.3", "123_a", "1.", ".5"])
def test_invalid_numeric_literal_is_consumed_as_one_error(text):
    with pytest.raises(LexerError) as caught:
        tokenize(text)
    assert len(caught.value.errors) == 1
    assert text in caught.value.errors[0].reason
    assert [token.type for token in caught.value.tokens] == [TokenType.EOF]


def test_errors_are_aggregated_and_sorted_while_valid_tokens_are_kept():
    with pytest.raises(LexerError) as caught:
        tokenize("SELECT @ FROM t;\n12abc")
    error = caught.value
    assert len(error.errors) == 2
    assert [(e.line, e.column) for e in error.errors] == [(1, 8), (2, 1)]
    assert [token.lexeme for token in error.tokens] == [
        "SELECT", "FROM", "t", ";", ""
    ]


def test_unterminated_string_and_block_comment_report_start_position():
    with pytest.raises(LexerError) as caught:
        tokenize("SELECT 'oops\nFROM t; /* unfinished")
    error = caught.value
    assert [(e.line, e.column) for e in error.errors] == [(1, 8), (2, 9)]


def test_double_equals_has_recovery_hint_and_no_equals_tokens():
    with pytest.raises(LexerError, match=r"did you mean '='\?") as caught:
        tokenize("a==b")
    assert [t.lexeme for t in caught.value.tokens] == ["a", "b", ""]
