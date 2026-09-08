"""D 模块测试共用的算术替身、假目录和假存储。"""

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ExecuteError, IntegerArithmeticError, SemanticError
from sql_compiler.lexer import Token, TokenType

INT_MIN = -2147483648
INT_MAX = 2147483647


def fake_checked_int_arithmetic(op: str, left: int, right: int) -> int:
    """与契约一致的 32 位有符号运算，供 C 的 types 尚未实现时使用。"""
    if op == "/" and right == 0:
        raise IntegerArithmeticError("division by zero")
    if op == "+":
        result = left + right
    elif op == "-":
        result = left - right
    elif op == "*":
        result = left * right
    elif op == "/":
        sign = -1 if (left < 0) != (right < 0) else 1
        result = sign * (abs(left) // abs(right))
    else:
        raise ValueError(op)
    if result < INT_MIN or result > INT_MAX:
        raise IntegerArithmeticError("integer arithmetic out of range")
    return result


def ensure_arithmetic(monkeypatch) -> None:
    """优先使用 C 的实现；存根阶段再注入契约算术。"""
    from sql_compiler.types import checked_int_arithmetic
    try:
        if checked_int_arithmetic("+", 1, 2) == 3:
            return
    except NotImplementedError:
        pass
    monkeypatch.setattr(
        "sql_compiler.types.checked_int_arithmetic",
        fake_checked_int_arithmetic,
    )


def tok(kind: TokenType, lexeme: str, line: int, column: int) -> Token:
    """构造测试 Token。"""
    return Token(kind, lexeme, line, column)


def eof(line: int, column: int) -> Token:
    return Token(TokenType.EOF, "", line, column)


def ident(name: str, line: int, column: int) -> dict:
    return {"node": "IdentifierExpr", "name": name, "line": line, "column": column}


def lit(value, lit_type: str, line: int, column: int) -> dict:
    return {
        "node": "LiteralExpr", "value": value, "lit_type": lit_type,
        "line": line, "column": column,
    }


def binary(op: str, left: dict, right: dict, line: int, column: int) -> dict:
    return {
        "node": "BinaryExpr", "op": op, "left": left, "right": right,
        "line": line, "column": column,
    }


def unary(op: str, operand: dict, line: int, column: int) -> dict:
    return {
        "node": "UnaryExpr", "op": op, "operand": operand,
        "line": line, "column": column,
    }


class FakeCatalog(Catalog):
    """内存目录替身，供计划、执行和编排测试注入。"""

    def __init__(self, json_path=None, *, storage=None) -> None:
        super().__init__(json_path, storage=storage)
        self.tables: dict[str, dict] = {}
        self.create_calls = 0

    def create_table(self, name: str, columns: list[ColumnDef]) -> None:
        self.create_calls += 1
        key = name.lower()
        if key in self.tables:
            column = columns[0] if columns else None
            line, column_no = (column.line, column.column) if column else (1, 1)
            raise SemanticError(line, column_no, f"table '{name}' already exists")
        self.tables[key] = {"name": name, "columns": list(columns)}
        if self.storage is not None and hasattr(self.storage, "create_table"):
            self.storage.create_table(name)

    def find_table(self, name: str):
        return self.tables.get(name.lower())

    def find_column(self, table: str, column: str):
        schema = self.find_table(table)
        if schema is None:
            return None
        key = column.lower()
        for item in schema["columns"]:
            if item.name.lower() == key:
                return item
        return None

    def get_type(self, table: str, column: str):
        found = self.find_column(table, column)
        return None if found is None else found.col_type

    def list_tables(self) -> list[str]:
        names = [schema["name"] for schema in self.tables.values()]
        return sorted(names, key=str.lower)

    def snapshot(self) -> "FakeCatalog":
        copy = FakeCatalog()
        copy.tables = {
            key: {"name": value["name"], "columns": list(value["columns"])}
            for key, value in self.tables.items()
        }
        return copy


class FakeStorage:
    """表级存储替身，记录插入、删除和刷盘。"""

    def __init__(self) -> None:
        self.tables: dict[str, list] = {}
        self.flushed = 0
        self.closed = 0
        self._slot = 0

    def has_table(self, table: str) -> bool:
        return table.lower() in self.tables

    def create_table(self, table: str) -> None:
        self.tables.setdefault(table.lower(), [])

    def insert_record(self, table: str, row: tuple, columns: list[ColumnDef]):
        key = table.lower()
        rid = (1, self._slot)
        self._slot += 1
        self.tables.setdefault(key, []).append([rid, row, False])
        return rid

    def scan_records(self, table: str, columns: list[ColumnDef]):
        for rid, row, tomb in self.tables.get(table.lower(), []):
            if not tomb:
                yield rid, row

    def delete_record(self, table: str, rid) -> None:
        for item in self.tables.get(table.lower(), []):
            if item[0] == rid:
                item[2] = True
                return
        raise ExecuteError("invalid record id")

    def flush(self) -> None:
        self.flushed += 1

    def close(self) -> None:
        self.closed += 1
