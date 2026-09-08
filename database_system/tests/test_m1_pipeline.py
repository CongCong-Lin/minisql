"""通过测试替身验证真实连接器的跨阶段数据传递。"""

from copy import deepcopy

import pytest

from engine import runtime
from sql_compiler import lexer
from sql_compiler.ast_nodes import ColumnDef, CreateTableStmt
from sql_compiler.catalog import Catalog
from sql_compiler.errors import SemanticError
from sql_compiler.lexer import Token, TokenType


def test_fake_pipeline_preserves_snapshots_and_does_not_commit(monkeypatch):
    """假词法输出进入真实连接器，阶段收到同一目录且快照不被污染。"""
    catalog = Catalog()
    text = "CREATE TABLE t(id INT);"
    tokens = [
        Token(TokenType.KEYWORD, "CREATE", 1, 1),
        Token(TokenType.KEYWORD, "TABLE", 1, 8),
        Token(TokenType.IDENTIFIER, "t", 1, 14),
        Token(TokenType.DELIMITER, "(", 1, 15),
        Token(TokenType.IDENTIFIER, "id", 1, 16),
        Token(TokenType.KEYWORD, "INT", 1, 19),
        Token(TokenType.DELIMITER, ")", 1, 22),
        Token(TokenType.DELIMITER, ";", 1, 23),
        Token(TokenType.EOF, "", 1, 24),
    ]
    stmt = CreateTableStmt("t", [ColumnDef("id", "INT", line=1, column=16)],
                           line=1, column=1)
    seen = []
    original = {"op": "CreateTable", "table": "t", "columns": [
        {"name": "id", "type": "INT", "line": 1, "column": 16},
    ]}
    output = deepcopy(original)

    def fake_tokenize(source):
        assert source == text
        seen.append("词法")
        return tokens

    def fake_parse(incoming):
        assert incoming is tokens
        seen.append("语法")
        return [stmt]

    def fake_analyze(stmts, incoming_catalog):
        assert incoming_catalog is catalog
        seen.append("语义")
        stmts[0].semantic_marker = "不得进入 JSON"
        return stmts

    def fake_plan(incoming, incoming_catalog):
        assert incoming is stmt and incoming_catalog is catalog
        seen.append("计划")
        return original

    def fake_optimize(incoming):
        assert incoming is original
        seen.append("优化")
        return output, []

    monkeypatch.setattr(lexer, "tokenize", fake_tokenize)
    monkeypatch.setattr(runtime.parser, "parse", fake_parse)
    monkeypatch.setattr(runtime.semantic, "analyze", fake_analyze)
    monkeypatch.setattr(runtime.planner, "plan", fake_plan)
    monkeypatch.setattr(runtime.optimizer, "optimize", fake_optimize)

    result = runtime._compile_statement(lexer.tokenize(text), catalog)
    assert seen == ["词法", "语法", "语义", "计划", "优化"]
    assert result.ok and result.semantic_ok
    assert result.exec_result is None
    assert result.ast[0]["columns"][0]["column"] == 16
    assert "semantic_marker" not in result.ast[0]
    assert result.tokens[-1]["type"] == "EOF"
    original["columns"][0]["name"] = "被后续操作改动"
    output["table"] = "被后续操作改动"
    tokens[0].lexeme = "被后续操作改动"
    assert result.plans[0]["columns"][0]["name"] == "id"
    assert result.plans[1]["table"] == "t"
    assert result.tokens[0]["lexeme"] == "CREATE"


def test_pipeline_propagates_original_error_without_entering_later_stage(monkeypatch):
    """底层连接器不能吞异常或在语义失败后继续生成计划。"""
    stmt = CreateTableStmt("t", [], line=1, column=1)
    problem = SemanticError(1, 1, "测试诊断")
    monkeypatch.setattr(runtime.parser, "parse", lambda tokens: [stmt])

    def fail(stmts, catalog):
        raise problem

    def forbidden(*args):
        pytest.fail("语义失败后不应调用计划生成")

    monkeypatch.setattr(runtime.semantic, "analyze", fail)
    monkeypatch.setattr(runtime.planner, "plan", forbidden)
    with pytest.raises(SemanticError) as caught:
        runtime._compile_statement([Token(TokenType.EOF, "", 1, 1)], Catalog())
    assert caught.value is problem
