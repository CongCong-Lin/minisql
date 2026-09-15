"""D 负责：生命周期与唯一 SQL 提交入口。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from engine.executor import execute
from sql_compiler import lexer, optimizer, parser, planner, semantic
from sql_compiler.ast_nodes import CreateTableStmt, Stmt
from sql_compiler.catalog import Catalog
from sql_compiler.errors import (
    CompileError, ExecuteError, LexerError, ParserError, PlannerError,
    SemanticError,
)
from sql_compiler.lexer import Token, TokenType
from storage.file_manager import PageStore
from storage.storage_engine import StorageEngine
from utils.results import StmtResult

_SYSTEM_CATALOG = "__catalog__"


def _open_legacy_database(data_dir: str, *, mode: str = "database"
                  ) -> tuple[Catalog, StorageEngine | None]:
    """按显式模式打开目录及存储，新库先引导系统目录。"""
    if mode not in ("compiler", "database"):
        raise ExecuteError(f"unsupported mode: {mode}")
    root = Path(data_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExecuteError(str(exc)) from exc
    if mode == "compiler":
        return Catalog(json_path=str(root / "catalog.json")), None

    db_path = root / "minisql.db"
    is_new = (not db_path.exists()) or db_path.stat().st_size == 0
    storage: StorageEngine | None = None
    try:
        pages = PageStore(str(db_path))
        storage = StorageEngine(pages)
        if is_new:
            storage.create_table(_SYSTEM_CATALOG)
            storage.flush()
        catalog = Catalog(storage=storage)
        return catalog, storage
    except Exception:
        if storage is not None:
            try:
                storage.close()
            except Exception:
                pass
        raise


def open_database(data_dir: str, *, mode: str = "database", username=None, password=None, timeout=5):
    """数据库入口统一创建受事务和权限保护的会话。"""
    if mode == "compiler":
        root = Path(data_dir)
        if (root / "minisql.db").exists():
            raise ExecuteError("编译模式需要独立目录，不能绕过数据库会话读取真实数据库")
        return _open_legacy_database(data_dir, mode=mode)
    if mode != "database":
        raise ExecuteError("不支持的运行模式")
    from engine.session import connect
    session = connect(data_dir, username=username, password=password, timeout=timeout)
    return session.catalog, session.storage


def run(text: str, catalog: Catalog,
        storage: StorageEngine | None = None) -> list[StmtResult]:
    """完整扫描并按分号分段，逐段编译、提交或执行，保留全部结果。"""
    session = getattr(catalog, "_session", None)
    if session is not None:
        return session.run(text)
    segments = _scan_and_segment(text)
    results: list[StmtResult] = []
    for segment in segments:
        compiled, origin, stmt = _compile_segment(segment, catalog)
        if not compiled.ok:
            results.append(compiled)
            continue
        if storage is None:
            results.append(_commit_compiler(compiled, stmt, catalog))
        else:
            results.append(_execute_database(compiled, storage, catalog))
    return results


def close_database(catalog: Catalog,
                   storage: StorageEngine | None) -> None:
    """数据库模式关闭存储，编译模式不额外提交目录。"""
    session = getattr(catalog, "_session", None)
    if session is not None:
        session.close()
        return
    if storage is not None:
        storage.close()


def _compile_sql(text: str, catalog: Catalog) -> list[StmtResult]:
    """实现公开纯编译入口，使用目录快照并在首个失败段抛错。"""
    session = getattr(catalog, "_session", None)
    if session is not None:
        diagnostics = session.inspect(text)
        if diagnostics:
            item = diagnostics[0]
            raise CompileError(item.stage, item.line, item.column, item.reason)
        catalog = session.catalog_snapshot()
    segments = _scan_and_segment(text)
    working = catalog.snapshot() if segments else catalog
    results: list[StmtResult] = []
    for segment in segments:
        compiled, origin, stmt = _compile_segment(segment, working)
        if not compiled.ok:
            if origin is None:
                raise CompileError("PLANNER", 1, 1, compiled.error or "compile failed")
            raise origin
        if isinstance(stmt, CreateTableStmt):
            working.create_table(stmt.table, stmt.columns)
        results.append(compiled)
    return results


def _compile_statement(tokens: list[Token], catalog: Catalog) -> StmtResult:
    """连接单语句的四个阶段，供 M1 测试替身验证接口。"""
    token_snapshot = [_token_dict(token) for token in tokens]
    stmts = parser.parse(tokens)
    if len(stmts) != 1:
        raise ValueError("内部单语句连接器必须接收恰好一条语句")
    ast_snapshot = [stmt.to_dict() for stmt in stmts]
    annotated = semantic.analyze(stmts, catalog)
    original = planner.plan(annotated[0], catalog)
    original_snapshot = deepcopy(original)
    optimized, _rules = optimizer.optimize(original)
    return StmtResult(
        ok=True, error=None, tokens=token_snapshot, ast=ast_snapshot,
        plans=[original_snapshot, deepcopy(optimized)], exec_result=None,
        semantic_ok=True,
    )


@dataclass
class _Segment:
    """一次分号切片得到的语句片段。"""

    tokens: list[Token]
    lexer_errors: list[LexerError]
    global_eof: Token


def _scan_and_segment(text: str) -> list[_Segment]:
    """只扫描一次，再按分号 Token 切片并归属词法诊断。"""
    try:
        tokens = lexer.tokenize(text)
        diagnostics: list[LexerError] = []
    except LexerError as exc:
        if exc.tokens is None:
            raise
        tokens = list(exc.tokens)
        if exc.errors:
            diagnostics = list(exc.errors)
        else:
            diagnostics = [LexerError(exc.line, exc.column, exc.reason)]
    if not tokens or tokens[-1].type != TokenType.EOF:
        tokens = list(tokens) + [Token(TokenType.EOF, "", 1, 1)]
    return _build_segments(tokens, diagnostics)


def _build_segments(tokens: list[Token], diagnostics: list[LexerError]) -> list[_Segment]:
    """按分号切分；空且无错误的尾片段不生成结果。"""
    eof = tokens[-1]
    body = [token for token in tokens if token.type != TokenType.EOF]
    chunks: list[list[Token]] = []
    current: list[Token] = []
    for token in body:
        current.append(token)
        if token.type == TokenType.DELIMITER and token.lexeme == ";":
            chunks.append(current)
            current = []
    has_tail_tokens = bool(current)
    if current:
        chunks.append(current)

    assigned = [[] for _ in chunks]
    leftover: list[LexerError] = []
    for diagnostic in diagnostics:
        index = _segment_index(chunks, diagnostic)
        if index is None:
            leftover.append(diagnostic)
        else:
            assigned[index].append(diagnostic)
    if leftover:
        if has_tail_tokens:
            assigned[-1].extend(leftover)
        else:
            chunks.append([])
            assigned.append(leftover)

    segments = [
        _Segment(tokens=chunk, lexer_errors=errors, global_eof=eof)
        for chunk, errors in zip(chunks, assigned)
        if chunk or errors
    ]
    return segments


def _segment_index(chunks: list[list[Token]], diagnostic: LexerError) -> int | None:
    """把诊断归入上一分号之后、当前分号之前的片段。"""
    position = (diagnostic.line, diagnostic.column)
    previous = (0, 0)
    for index, chunk in enumerate(chunks):
        if chunk and chunk[-1].type == TokenType.DELIMITER and chunk[-1].lexeme == ";":
            end = (chunk[-1].line, chunk[-1].column)
            if previous < position < end:
                return index
            previous = end
        else:
            if position > previous:
                return index
            previous = (chunk[-1].line, chunk[-1].column) if chunk else previous
    return None


def _parse_tokens(segment: _Segment) -> list[Token]:
    """为 Parser 准备带局部 EOF 的 Token 列表，不修改全局流。"""
    if segment.tokens and segment.tokens[-1].type == TokenType.DELIMITER and segment.tokens[-1].lexeme == ";":
        semi = segment.tokens[-1]
        eof = Token(TokenType.EOF, "", semi.line, semi.column + 1)
    else:
        eof = Token(
            TokenType.EOF, "",
            segment.global_eof.line, segment.global_eof.column,
        )
    return list(segment.tokens) + [eof]


def _compile_segment(segment: _Segment, catalog: Catalog
                     ) -> tuple[StmtResult, CompileError | None, Stmt | None]:
    """编译一个分段；失败时同时保留 StmtResult 与原始异常。"""
    parse_tokens = _parse_tokens(segment)
    token_snapshot = [_token_dict(token) for token in parse_tokens]
    if segment.lexer_errors:
        error = LexerError(
            0, 0, "",
            errors=list(segment.lexer_errors),
            tokens=parse_tokens,
        )
        return _failed_result(error, None, None, None, None), error, None
    try:
        stmts = parser.parse(parse_tokens)
    except ParserError as exc:
        return _failed_result(exc, token_snapshot, None, None, None), exc, None
    if len(stmts) != 1:
        loc = parse_tokens[0]
        exc = ParserError(loc.line, loc.column, "expected a single statement")
        return _failed_result(exc, token_snapshot, None, None, None), exc, None
    ast_snapshot = [stmt.to_dict() for stmt in stmts]
    try:
        annotated = semantic.analyze(stmts, catalog)
    except SemanticError as exc:
        return _failed_result(exc, token_snapshot, ast_snapshot, False, None), exc, None
    try:
        original = planner.plan(annotated[0], catalog)
    except PlannerError as exc:
        return _failed_result(exc, token_snapshot, ast_snapshot, True, None), exc, None
    original_snapshot = deepcopy(original)
    try:
        optimized, _rules = optimizer.optimize(original)
    except PlannerError as exc:
        return (
            _failed_result(exc, token_snapshot, ast_snapshot, True, [original_snapshot]),
            exc, None,
        )
    result = StmtResult(
        ok=True, error=None, tokens=token_snapshot, ast=ast_snapshot,
        plans=[original_snapshot, deepcopy(optimized)], exec_result=None,
        semantic_ok=True,
    )
    return result, None, annotated[0]


def _commit_compiler(compiled: StmtResult, stmt: Stmt | None, catalog: Catalog) -> StmtResult:
    """阶段一：成功 CREATE 由 run 提交传入的 Catalog。"""
    if not isinstance(stmt, CreateTableStmt):
        return compiled
    try:
        catalog.create_table(stmt.table, stmt.columns)
    except SemanticError as exc:
        return _failed_result(exc, compiled.tokens, compiled.ast, True, compiled.plans)
    except ExecuteError as exc:
        return _execute_failure(compiled, exc)
    return compiled


def _execute_database(compiled: StmtResult, storage: StorageEngine, catalog: Catalog) -> StmtResult:
    """数据库模式执行优化计划。"""
    try:
        exec_result = execute(compiled.plans[1], catalog, storage)
    except ExecuteError as exc:
        return _execute_failure(compiled, exc)
    return StmtResult(
        ok=True, error=None, tokens=compiled.tokens, ast=compiled.ast,
        plans=compiled.plans, exec_result=exec_result, semantic_ok=True,
    )


def _failed_result(error: CompileError, tokens, ast, semantic_ok, plans) -> StmtResult:
    """按已完成阶段填充失败结果。"""
    return StmtResult(
        ok=False,
        error=_format_compile_error(error),
        tokens=tokens,
        ast=ast,
        plans=list(plans) if plans else [],
        exec_result=None,
        semantic_ok=semantic_ok,
    )


def _execute_failure(compiled: StmtResult, exc: ExecuteError) -> StmtResult:
    """执行或目录提交失败时保留两份计划。"""
    return StmtResult(
        ok=False,
        error=f"[EXECUTE] Error: {_escape_reason(exc.message)}",
        tokens=compiled.tokens,
        ast=compiled.ast,
        plans=compiled.plans,
        exec_result=None,
        semantic_ok=True,
    )


def _format_compile_error(error: CompileError) -> str:
    """展开全部诊断，按源码位置排序后用换行连接。"""
    items: list[CompileError]
    if isinstance(error, (LexerError, SemanticError)) and error.errors:
        items = list(error.errors)
    else:
        items = [error]
    items.sort(key=lambda item: (item.line, item.column))
    return "\n".join(
        f"[{item.stage}] Error at line {item.line}, column {item.column}: "
        f"{_escape_reason(item.reason)}"
        for item in items
    )


def _escape_reason(reason: str) -> str:
    """把原因中的控制字符转成可见形式，保证一行一条诊断。"""
    pieces: list[str] = []
    for char in reason:
        code = ord(char)
        if char == "\\":
            pieces.append("\\\\")
        elif char == "\n":
            pieces.append("\\n")
        elif char == "\r":
            pieces.append("\\r")
        elif char == "\t":
            pieces.append("\\t")
        elif code < 32 or code == 127:
            pieces.append(f"\\x{code:02x}")
        else:
            pieces.append(char)
    return "".join(pieces)


def _token_dict(token: Token) -> dict:
    """序列化供展示和 Parser 回溯使用的 Token 字典。"""
    return {
        "type": token.type.name,
        "lexeme": token.lexeme,
        "line": token.line,
        "column": token.column,
    }
