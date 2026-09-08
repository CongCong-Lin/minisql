"""M1 验证公共数据、异常身份和声明接口，不冒充 SQL 验收。"""

import ast
import importlib
import inspect
import json
from pathlib import Path

import pytest

from sql_compiler.ast_nodes import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, IdentifierExpr,
    InsertStmt, LiteralExpr, SelectStmt, UnaryExpr,
)
from sql_compiler.errors import (
    CompileError, ExecuteError, LexerError, ParserError, PlannerError,
    SemanticError,
)
from sql_compiler.lexer import Token, TokenType
from storage.page import Page
from utils.results import ExecuteResult, StmtResult

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "plan" / "handoff" / "contracts.md"


def test_ast_snapshot_keeps_structure_but_not_semantic_annotations():
    """嵌套结构应独立序列化，不带入语义阶段新增属性。"""
    name = IdentifierExpr("ID", line=2, column=30)
    value = LiteralExpr(1, "INT", line=2, column=35)
    expr = BinaryExpr("=", name, value, line=2, column=33)
    stmt = SelectStmt(["ID"], "T", expr, line=2, column=1)
    before = stmt.to_dict()
    name.resolved_type = "INT"
    name.expr_type = "INT"
    expr.expr_type = "BOOL"
    assert stmt.to_dict() == before
    assert before["where"]["line"] == 2
    assert before["where"]["column"] == 33
    assert before["where"]["left"]["name"] == "ID"
    stmt.columns.append("name")
    assert before["columns"] == ["ID"]
    json.dumps(before, allow_nan=False)


def test_all_statement_and_expression_shapes_support_keyword_positions():
    """各人都能用约定的字段和位置构造假 AST。"""
    col = ColumnDef("id", "INT", line=1, column=16)
    lit = LiteralExpr(True, "BOOL", line=1, column=25)
    nodes = [
        CreateTableStmt("t", [col], line=1, column=1),
        InsertStmt("t", ["id"], [LiteralExpr(1, "INT", line=1, column=28)],
                   line=1, column=1),
        SelectStmt("*", "t", None, line=1, column=1),
        DeleteStmt("t", UnaryExpr("NOT", lit, line=1, column=21),
                   line=1, column=1),
    ]
    assert [node.to_dict()["node"] for node in nodes] == [
        "CreateTableStmt", "InsertStmt", "SelectStmt", "DeleteStmt",
    ]
    assert nodes[2].to_dict()["where"] is None


@pytest.mark.parametrize("error_class,stage", [
    (LexerError, "LEXER"), (ParserError, "PARSER"),
    (SemanticError, "SEMANTIC"), (PlannerError, "PLANNER"),
])
def test_public_compile_error_identity(error_class, stage):
    """所有阶段错误都能被同一个 CompileError 捕获。"""
    with pytest.raises(CompileError) as caught:
        raise error_class(3, 7, "诊断")
    assert str(caught.value) == f"[{stage}] Error at line 3, column 7: 诊断"
    assert not issubclass(ExecuteError, CompileError)


def test_lexer_aggregation_keeps_tokens_and_first_diagnostic():
    """完整扫描异常保留恢复所需的 Token 与全部单条诊断。"""
    diagnostics = [
        LexerError(2, 4, "非法字符"),
        LexerError(4, 1, "未闭合字符串"),
    ]
    tokens = [Token(TokenType.EOF, "", 4, 9)]
    error = LexerError(1, 1, "占位", errors=diagnostics, tokens=tokens)
    assert (error.line, error.column, error.reason) == (2, 4, "非法字符")
    assert error.errors == diagnostics
    assert error.tokens == tokens
    diagnostics.clear()
    tokens.clear()
    assert len(error.errors) == 2
    assert len(error.tokens) == 1


def test_page_buffers_do_not_share_mutable_data():
    """不同页不能因为默认参数共享同一个字节缓冲。"""
    first, second = Page(1), Page(2)
    first.data[16] = 42
    first.dirty = True
    assert len(first.data) == len(second.data) == 4096
    assert second.data[16] == 0
    assert second.dirty is False


def test_result_can_represent_compile_success_and_partial_failure():
    """没有执行结果的成功和已通过语义的执行失败必须可区分。"""
    compiled = StmtResult(True, None, [], [], [{}, {}], None, True)
    failed = StmtResult(False, "[EXECUTE] Error: 故障", [], [], [{}, {}], None, True)
    assert compiled.ok and compiled.exec_result is None
    assert failed.semantic_ok and not failed.ok and len(failed.plans) == 2
    result = ExecuteResult([(1,)], "1 row selected", ["id"])
    assert result.rows[0] == (1,)


def test_public_signatures_match_authoritative_document():
    """直接从权威契约读取函数签名，防止骨架与文档各自变化。"""
    import re
    from engine import runtime
    from storage.file_manager import PageStore
    from storage.storage_engine import StorageEngine
    from sql_compiler.catalog import Catalog
    from sql_compiler.lexer import tokenize
    from storage.record import serialize, deserialize

    objects = {
        "Catalog": Catalog, "PageStore": PageStore, "StorageEngine": StorageEngine,
        "tokenize": tokenize, "serialize": serialize, "deserialize": deserialize,
        "open_database": runtime.open_database, "run": runtime.run,
        "close_database": runtime.close_database,
    }
    verified = 0
    text = CONTRACT.read_text(encoding="utf-8")
    for source in re.findall(r"```python\n(.*?)```", text, re.S):
        for node in ast.parse(source).body:
            if isinstance(node, ast.ClassDef) and node.name in objects:
                pairs = [(member, getattr(objects[node.name], member.name))
                         for member in node.body if isinstance(member, ast.FunctionDef)]
            elif isinstance(node, ast.FunctionDef) and node.name in objects:
                pairs = [(node, objects[node.name])]
            else:
                continue
            for declared, implementation in pairs:
                namespace = {}
                code = "from __future__ import annotations\n" + ast.unparse(declared)
                exec(code, namespace)
                wanted = inspect.signature(namespace[declared.name])
                actual = inspect.signature(implementation)
                wanted_params = [(p.name, p.kind, p.default)
                                 for p in wanted.parameters.values()]
                actual_params = [(p.name, p.kind, p.default)
                                 for p in actual.parameters.values()]
                assert actual_params == wanted_params, declared.name
                verified += 1
    assert verified >= 25
