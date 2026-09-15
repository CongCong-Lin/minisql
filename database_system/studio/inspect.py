"""编辑器实时诊断：编译但不执行，给出可标红的源码区间。"""

from __future__ import annotations

from dataclasses import dataclass

from sql_compiler.ast_nodes import CreateTableStmt
from sql_compiler.catalog import Catalog
from sql_compiler.errors import CompileError, ExecuteError, LexerError, SemanticError


@dataclass(frozen=True)
class Diagnostic:
    """一条带源码位置的编译诊断。"""

    stage: str
    line: int
    column: int
    reason: str
    start: str
    end: str

    @property
    def message(self) -> str:
        return f"[{self.stage}] Error at line {self.line}, column {self.column}: {self.reason}"


def inspect_sql(text: str, catalog: Catalog | None = None) -> list[Diagnostic]:
    """用目录快照编译全部语句，不写入真实 Catalog / 存储。"""
    from engine.runtime import _compile_segment, _scan_and_segment

    session = getattr(catalog, "_session", None)
    if session is not None:
        return session.inspect(text)

    if not text.strip():
        return []
    working = catalog.snapshot() if catalog is not None else Catalog()
    try:
        segments = _scan_and_segment(text)
    except CompileError as exc:
        return _from_error(text, exc, None)

    found: list[Diagnostic] = []
    for segment in segments:
        compiled, origin, stmt = _compile_segment(segment, working)
        if origin is not None:
            found.extend(_from_error(text, origin, compiled.tokens))
            continue
        if isinstance(stmt, CreateTableStmt):
            try:
                working.create_table(stmt.table, stmt.columns)
            except SemanticError as exc:
                found.extend(_from_error(text, exc, compiled.tokens))
            except ExecuteError as exc:
                found.append(_diagnostic(text, "EXECUTE", stmt.line, stmt.column, str(exc), None))
    return found


def _from_error(text: str, exc: CompileError, tokens: list[dict] | None) -> list[Diagnostic]:
    items: list[CompileError]
    if isinstance(exc, (LexerError, SemanticError)) and exc.errors:
        items = list(exc.errors)
    else:
        items = [exc]
    return [
        _diagnostic(text, item.stage, item.line, item.column, item.reason, tokens)
        for item in items
    ]


def _diagnostic(text: str, stage: str, line: int, column: int, reason: str,
                tokens: list[dict] | None) -> Diagnostic:
    start, end = _span(text, line, column, _lexeme_at(tokens, line, column))
    return Diagnostic(stage, line, column, reason, start, end)


def _lexeme_at(tokens: list[dict] | None, line: int, column: int) -> str | None:
    if not tokens:
        return None
    for token in tokens:
        if token.get("line") == line and token.get("column") == column:
            lexeme = token.get("lexeme") or ""
            if token.get("type") == "EOF" or not lexeme:
                return None
            return str(lexeme)
    return None


def _span(text: str, line: int, column: int, lexeme: str | None) -> tuple[str, str]:
    rows = text.split("\n") or [""]
    if line < 1:
        line = 1
    if line > len(rows):
        line = len(rows)
    row = rows[line - 1]
    start_col = max(0, column - 1)
    if start_col > len(row):
        start_col = len(row)
    if lexeme:
        end_col = start_col + max(1, len(lexeme))
    else:
        end_col = start_col
        if end_col < len(row) and (row[end_col].isalnum() or row[end_col] == "_"):
            while end_col < len(row) and (row[end_col].isalnum() or row[end_col] == "_"):
                end_col += 1
        else:
            end_col = start_col + 1
    if end_col <= start_col:
        end_col = start_col + 1
    return f"{line}.{start_col}", f"{line}.{end_col}"
