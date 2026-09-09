"""核对 C 公共用例的语义与静态位置；不声称完成命令行验收。"""

import json
from pathlib import Path

import pytest

from sql_compiler import ast_nodes
from sql_compiler.catalog import Catalog
from sql_compiler.errors import SemanticError
from sql_compiler.semantic import analyze


CASES = Path(__file__).parent / "cases" / "compiler"
FILES = sorted(CASES.glob("c[0-9][0-9]_*.sql"))


def decode(value):
    """从人工预期的 AST 结构还原公共节点，不解析 SQL。"""
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, dict):
        fields = {key: decode(item) for key, item in value.items() if key != "node"}
        return getattr(ast_nodes, value["node"])(**fields)
    return value


def test_c_case_count_and_error_ratio():
    assert len(FILES) == 16
    exits = [path.with_suffix(".expected").read_text(encoding="utf-8").splitlines()[-1]
             for path in FILES]
    assert exits.count("EXIT: 0") == 6
    assert exits.count("EXIT: 1") == 10


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.stem)
def test_public_case_semantics_and_source_positions(path):
    source = path.read_text(encoding="utf-8").splitlines()
    expected = path.with_suffix(".expected").read_text(encoding="utf-8").splitlines()
    catalog = Catalog()
    actual_errors = []
    actual_successes = 0
    for line in expected:
        if line.startswith("TOKENS: "):
            tokens = json.loads(line.removeprefix("TOKENS: "))
            assert [token["type"] for token in tokens].count("EOF") == 1
            assert tokens[-1]["type"] == "EOF"
            for token in tokens:
                text = source[token["line"] - 1]
                start = token["column"] - 1
                assert text[start:start + len(token["lexeme"])] == token["lexeme"]
            assert tokens[-1]["column"] == len(source[tokens[-1]["line"] - 1]) + 1
        elif line.startswith("AST: "):
            structure = json.loads(line.removeprefix("AST: "))
            stmts = decode(structure)
            try:
                analyze(stmts, catalog)
            except SemanticError as error:
                actual_errors.extend(str(item) for item in (error.errors or [error]))
            else:
                actual_successes += 1
                for stmt in stmts:
                    if isinstance(stmt, ast_nodes.CreateTableStmt):
                        catalog.create_table(stmt.table, stmt.columns)
            assert [stmt.to_dict() for stmt in stmts] == structure
    assert actual_errors == [line for line in expected if line.startswith("[SEMANTIC]")]
    assert actual_successes == expected.count("SEMANTIC: OK")
    assert expected[-1] == f"EXIT: {int(bool(actual_errors))}"
