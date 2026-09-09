"""D 负责：分段恢复、目录提交时点、生命周期和纯编译入口。"""

import pytest

from engine import runtime
from sql_compiler import compile_sql
from sql_compiler.ast_nodes import ColumnDef, CreateTableStmt, SelectStmt
from sql_compiler.errors import (
    ExecuteError, LexerError, ParserError, SemanticError,
)
from sql_compiler.lexer import TokenType
from tests.d_support import (
    FakeCatalog, FakeStorage, eof, ensure_arithmetic, tok,
)


@pytest.fixture(autouse=True)
def _arithmetic(monkeypatch):
    ensure_arithmetic(monkeypatch)


def _tokens_create() -> list:
    return [
        tok(TokenType.KEYWORD, "CREATE", 1, 1),
        tok(TokenType.KEYWORD, "TABLE", 1, 8),
        tok(TokenType.IDENTIFIER, "t", 1, 14),
        tok(TokenType.DELIMITER, "(", 1, 15),
        tok(TokenType.IDENTIFIER, "id", 1, 16),
        tok(TokenType.KEYWORD, "INT", 1, 19),
        tok(TokenType.DELIMITER, ")", 1, 22),
        tok(TokenType.DELIMITER, ";", 1, 23),
        eof(1, 24),
    ]


def _stmt_create() -> CreateTableStmt:
    return CreateTableStmt(
        "t", [ColumnDef("id", "INT", line=1, column=16)],
        line=1, column=1,
    )


def _stmt_select() -> SelectStmt:
    return SelectStmt("*", "t", None, line=1, column=1)


def _install(monkeypatch, tokenize_fn, parse_fn, analyze_fn=None):
    monkeypatch.setattr(runtime.lexer, "tokenize", tokenize_fn)
    monkeypatch.setattr(runtime.parser, "parse", parse_fn)
    monkeypatch.setattr(
        runtime.semantic, "analyze",
        analyze_fn or (lambda stmts, catalog: stmts),
    )


def test_empty_input_returns_no_results(monkeypatch):
    """空输入合法，返回空列表且不进入 Parser。"""
    def tokenize(text):
        assert text == ""
        return [eof(1, 1)]

    def forbidden(*args):
        pytest.fail("空输入不应调用 Parser")

    _install(monkeypatch, tokenize, forbidden)
    assert runtime.run("", FakeCatalog()) == []


def test_run_commits_create_in_compiler_mode(monkeypatch):
    """阶段一 CREATE 由 run 提交真实 Catalog，不写执行结果。"""
    catalog = FakeCatalog()
    stmt = _stmt_create()

    _install(monkeypatch, lambda text: _tokens_create(), lambda tokens: [stmt])
    results = runtime.run("CREATE TABLE t(id INT);", catalog)
    assert len(results) == 1
    assert results[0].ok and results[0].semantic_ok
    assert results[0].exec_result is None
    assert catalog.find_table("t") is not None
    assert catalog.create_calls == 1
    assert results[0].plans[0]["op"] == "CreateTable"
    assert results[0].plans[1]["op"] == "CreateTable"


def test_compile_sql_uses_snapshot_and_does_not_write(monkeypatch):
    """纯编译成功建表只对快照可见，真实目录不变。"""
    catalog = FakeCatalog()
    stmt = _stmt_create()
    _install(monkeypatch, lambda text: _tokens_create(), lambda tokens: [stmt])
    results = compile_sql("CREATE TABLE t(id INT);", catalog)
    assert results[0].ok
    assert catalog.find_table("t") is None
    assert catalog.create_calls == 0


def test_compile_sql_raises_first_parser_error(monkeypatch):
    """纯编译在首个失败段抛原始异常，不返回失败结果。"""
    problem = ParserError(1, 1, "unexpected token ';', expected: CREATE")

    def tokenize(text):
        return [tok(TokenType.DELIMITER, ";", 1, 1), eof(1, 2)]

    def parse(tokens):
        raise problem

    _install(monkeypatch, tokenize, parse)
    with pytest.raises(ParserError) as caught:
        compile_sql(";", FakeCatalog())
    assert caught.value is problem


def test_compile_sql_does_not_reraise_global_lexer_before_earlier_error(monkeypatch):
    """更早分段的语法错误优先于后续词法错误。"""
    create_tokens = _tokens_create()[:-1]  # 去掉全局 EOF，后面拼接
    tokens = create_tokens + [
        tok(TokenType.IDENTIFIER, "bad", 2, 1),
        tok(TokenType.DELIMITER, ";", 2, 5),
        eof(2, 6),
    ]
    lexer_error = LexerError(
        1, 1, "占位",
        errors=[LexerError(2, 1, "非法标识")],
        tokens=tokens,
    )
    parse_error = ParserError(1, 14, "unexpected token 't'")

    def tokenize(text):
        raise lexer_error

    def parse(incoming):
        lexemes = [item.lexeme for item in incoming]
        if "CREATE" in lexemes:
            raise parse_error
        pytest.fail("词法失败段不应进入 Parser")

    _install(monkeypatch, tokenize, parse)
    with pytest.raises(ParserError) as caught:
        compile_sql("CREATE TABLE t(id INT); bad;", FakeCatalog())
    assert caught.value is parse_error


def test_run_continues_after_lexer_error(monkeypatch):
    """含词法错误的段失败且 tokens 为 None，后续语句继续。"""
    tokens = [
        tok(TokenType.DELIMITER, ";", 1, 2),
        tok(TokenType.KEYWORD, "SELECT", 1, 4),
        tok(TokenType.OPERATOR, "*", 1, 11),
        tok(TokenType.KEYWORD, "FROM", 1, 13),
        tok(TokenType.IDENTIFIER, "t", 1, 18),
        tok(TokenType.DELIMITER, ";", 1, 19),
        eof(1, 20),
    ]
    aggregated = LexerError(
        1, 1, "占位",
        errors=[LexerError(1, 1, "非法字符 '@'")],
        tokens=tokens,
    )
    catalog = FakeCatalog()
    catalog.create_table("t", [ColumnDef("id", "INT", line=1, column=16)])

    def tokenize(text):
        raise aggregated

    def parse(incoming):
        assert incoming[0].lexeme.upper() == "SELECT"
        return [_stmt_select()]

    _install(monkeypatch, tokenize, parse)
    results = runtime.run("@; SELECT * FROM t;", catalog)
    assert len(results) == 2
    assert not results[0].ok
    assert results[0].tokens is None
    assert results[0].error.startswith("[LEXER] Error at line 1, column 1:")
    assert results[1].ok
    assert results[1].tokens[-1]["type"] == "EOF"


def test_missing_semicolon_fails_whole_segment(monkeypatch):
    """缺分号使截至下一分号的整段失败，不能拆出第二个语句。"""
    tokens = [
        tok(TokenType.KEYWORD, "SELECT", 1, 1),
        tok(TokenType.OPERATOR, "*", 1, 8),
        tok(TokenType.KEYWORD, "FROM", 1, 10),
        tok(TokenType.IDENTIFIER, "t", 1, 15),
        tok(TokenType.KEYWORD, "SELECT", 2, 1),
        tok(TokenType.OPERATOR, "*", 2, 8),
        tok(TokenType.KEYWORD, "FROM", 2, 10),
        tok(TokenType.IDENTIFIER, "t2", 2, 15),
        tok(TokenType.DELIMITER, ";", 2, 17),
        tok(TokenType.KEYWORD, "SELECT", 3, 1),
        tok(TokenType.OPERATOR, "*", 3, 8),
        tok(TokenType.KEYWORD, "FROM", 3, 10),
        tok(TokenType.IDENTIFIER, "t3", 3, 15),
        tok(TokenType.DELIMITER, ";", 3, 17),
        eof(3, 18),
    ]
    parsed = []

    def parse(incoming):
        starts = [item for item in incoming
                  if item.type == TokenType.KEYWORD and item.lexeme.upper() in
                  {"SELECT", "CREATE", "INSERT", "DELETE"}]
        parsed.append(len(starts))
        if len(starts) >= 2:
            second = starts[1]
            raise ParserError(
                second.line, second.column,
                "unexpected token 'SELECT', expected: ';'",
            )
        names = [item.lexeme for item in incoming if item.type == TokenType.IDENTIFIER]
        table = names[-1] if names else "t"
        return [SelectStmt("*", table, None,
                           line=starts[0].line, column=starts[0].column)]

    catalog = FakeCatalog()
    catalog.create_table("t", [ColumnDef("id", "INT", line=1, column=1)])
    catalog.create_table("t2", [ColumnDef("id", "INT", line=1, column=1)])
    catalog.create_table("t3", [ColumnDef("id", "INT", line=1, column=1)])
    _install(monkeypatch, lambda text: tokens, parse)
    results = runtime.run(
        "SELECT * FROM t\nSELECT * FROM t2;\nSELECT * FROM t3;", catalog,
    )
    assert len(results) == 2
    assert not results[0].ok
    assert "expected: ';'" in results[0].error
    assert results[1].ok
    assert parsed[0] == 2


def test_execute_failure_keeps_plans_and_continues(monkeypatch):
    """执行失败保留两份计划，后续语句仍处理。"""
    select_tokens = [
        tok(TokenType.KEYWORD, "SELECT", 1, 1),
        tok(TokenType.OPERATOR, "*", 1, 8),
        tok(TokenType.KEYWORD, "FROM", 1, 10),
        tok(TokenType.IDENTIFIER, "t", 1, 15),
        tok(TokenType.DELIMITER, ";", 1, 16),
        tok(TokenType.KEYWORD, "SELECT", 1, 18),
        tok(TokenType.OPERATOR, "*", 1, 25),
        tok(TokenType.KEYWORD, "FROM", 1, 27),
        tok(TokenType.IDENTIFIER, "t", 1, 32),
        tok(TokenType.DELIMITER, ";", 1, 33),
        eof(1, 34),
    ]
    calls = {"n": 0}

    def parse(incoming):
        return [_stmt_select()]

    def boom(plan, catalog, storage):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ExecuteError("disk full")
        from utils.results import ExecuteResult
        return ExecuteResult([], "0 rows selected", ["id"])

    catalog = FakeCatalog()
    catalog.create_table("t", [ColumnDef("id", "INT", line=1, column=16)])
    storage = FakeStorage()
    storage.create_table("t")
    _install(monkeypatch, lambda text: select_tokens, parse)
    monkeypatch.setattr(runtime, "execute", boom)
    results = runtime.run("SELECT * FROM t; SELECT * FROM t;", catalog, storage)
    assert len(results) == 2
    assert not results[0].ok
    assert results[0].error == "[EXECUTE] Error: disk full"
    assert len(results[0].plans) == 2
    assert results[0].semantic_ok is True
    assert results[1].ok
    assert results[1].exec_result.message == "0 rows selected"


def test_database_create_goes_through_executor_once(monkeypatch):
    """数据库模式由执行器调用一次 Catalog.create_table。"""
    catalog = FakeCatalog()
    storage = FakeStorage()
    catalog.storage = storage
    stmt = _stmt_create()
    _install(monkeypatch, lambda text: _tokens_create(), lambda tokens: [stmt])
    results = runtime.run("CREATE TABLE t(id INT);", catalog, storage)
    assert results[0].ok
    assert results[0].exec_result.message == "OK"
    assert catalog.create_calls == 1
    assert storage.flushed >= 1


def test_semantic_failure_keeps_ast_without_plans(monkeypatch):
    """语义失败保留 Token 和 AST，不生成计划。"""
    problem = SemanticError(1, 14, "table 't' already exists")

    def analyze(stmts, catalog):
        raise problem

    _install(
        monkeypatch, lambda text: _tokens_create(),
        lambda tokens: [_stmt_create()], analyze,
    )
    results = runtime.run("CREATE TABLE t(id INT);", FakeCatalog())
    assert not results[0].ok
    assert results[0].tokens is not None
    assert results[0].ast is not None
    assert results[0].semantic_ok is False
    assert results[0].plans == []


def test_error_reason_escapes_newlines(monkeypatch):
    """原因中的换行必须转义，不能拆成多条诊断。"""
    problem = ParserError(1, 1, "unexpected\ntoken")

    def tokenize(text):
        return [tok(TokenType.DELIMITER, ";", 1, 1), eof(1, 2)]

    def parse(tokens):
        raise problem

    _install(monkeypatch, tokenize, parse)
    results = runtime.run(";", FakeCatalog())
    assert "\n" not in results[0].error.split(": ", 1)[1]
    assert "\\n" in results[0].error


def test_open_database_rejects_unknown_mode():
    """生命周期只接受 compiler 与 database。"""
    with pytest.raises(ExecuteError, match="unsupported mode"):
        runtime.open_database("data", mode="hybrid")


def test_open_database_compiler_does_not_create_json(tmp_path):
    """编译模式只绑定 catalog.json 路径，不隐式写盘。"""
    catalog, storage = runtime.open_database(str(tmp_path), mode="compiler")
    assert storage is None
    assert catalog.json_path.endswith("catalog.json")
    assert list(tmp_path.iterdir()) == []


def test_open_database_new_db_creates_system_catalog(tmp_path, monkeypatch):
    """新库在构造 Catalog 前创建系统目录并刷盘。"""
    created = []

    class StubPages:
        def __init__(self, path, capacity=64, policy="LRU"):
            self.path = path

        def close(self):
            pass

    class StubStorage:
        def __init__(self, pages):
            self.pages = pages
            self.closed = False

        def create_table(self, name):
            created.append(name)

        def flush(self):
            created.append("flush")

        def close(self):
            self.closed = True

    class StubCatalog:
        def __init__(self, json_path=None, *, storage=None):
            self.json_path = json_path
            self.storage = storage

    monkeypatch.setattr(runtime, "PageStore", StubPages)
    monkeypatch.setattr(runtime, "StorageEngine", StubStorage)
    monkeypatch.setattr(runtime, "Catalog", StubCatalog)
    catalog, storage = runtime.open_database(str(tmp_path), mode="database")
    assert created == ["__catalog__", "flush"]
    assert catalog.storage is storage


def test_open_database_existing_file_does_not_recreate_catalog(tmp_path, monkeypatch):
    """已有非空数据库不得静默重建系统表。"""
    (tmp_path / "minisql.db").write_bytes(b"MSQL" + b"\x00" * 12)
    created = []

    class StubPages:
        def __init__(self, path, capacity=64, policy="LRU"):
            self.path = path

    class StubStorage:
        def __init__(self, pages):
            self.pages = pages

        def create_table(self, name):
            created.append(name)

        def flush(self):
            pass

        def close(self):
            pass

    class StubCatalog:
        def __init__(self, json_path=None, *, storage=None):
            self.storage = storage

    monkeypatch.setattr(runtime, "PageStore", StubPages)
    monkeypatch.setattr(runtime, "StorageEngine", StubStorage)
    monkeypatch.setattr(runtime, "Catalog", StubCatalog)
    runtime.open_database(str(tmp_path), mode="database")
    assert created == []


def test_open_database_closes_storage_on_bootstrap_failure(tmp_path, monkeypatch):
    """初始化中途失败时关闭已打开资源，并保留原始错误。"""
    closed = []

    class StubPages:
        def __init__(self, path, capacity=64, policy="LRU"):
            self.path = path

    class StubStorage:
        def __init__(self, pages):
            self.pages = pages

        def create_table(self, name):
            raise ExecuteError("disk full")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(runtime, "PageStore", StubPages)
    monkeypatch.setattr(runtime, "StorageEngine", StubStorage)
    with pytest.raises(ExecuteError, match="disk full"):
        runtime.open_database(str(tmp_path), mode="database")
    assert closed == [True]


def test_close_database_compiler_is_noop():
    """编译模式关闭不额外提交目录。"""
    runtime.close_database(FakeCatalog(), None)


def test_close_database_closes_storage():
    """数据库模式关闭委托存储引擎。"""
    storage = FakeStorage()
    runtime.close_database(FakeCatalog(), storage)
    assert storage.closed == 1
    runtime.close_database(FakeCatalog(), storage)
    assert storage.closed == 2


def test_optimize_snapshot_is_independent(monkeypatch):
    """编排保存的计划快照不能被后续原地修改污染。"""
    stmt = _stmt_create()
    _install(monkeypatch, lambda text: _tokens_create(), lambda tokens: [stmt])
    results = runtime.run("CREATE TABLE t(id INT);", FakeCatalog())
    results[0].plans[0]["table"] = "mutated"
    results[0].plans[1]["table"] = "mutated"
    # 重新编译一份对照
    again = runtime.run("CREATE TABLE t(id INT);", FakeCatalog())
    assert again[0].plans[0]["table"] == "t"
