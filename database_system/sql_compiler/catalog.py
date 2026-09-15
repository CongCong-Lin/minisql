"""C 负责：名称解析、目录快照和两种持久化后端。"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING, TypedDict

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError, SemanticError
from sql_compiler.types import INT_MAX, STORAGE_TYPES

if TYPE_CHECKING:
    from storage.storage_engine import StorageEngine


class TableSchema(TypedDict):
    """列的定义顺序就是记录存储顺序。"""

    name: str
    columns: list[ColumnDef]


_KEYWORDS = frozenset("SELECT FROM WHERE CREATE TABLE INSERT INTO VALUES DELETE "
                      "UPDATE SET ORDER BY GROUP JOIN AND OR NOT NULL "
                      "INT VARCHAR TRUE FALSE".split())
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_SYSTEM_TABLE = "__catalog__"


def _system_columns() -> list[ColumnDef]:
    """系统结构直接定义，不查询自身；每次返回独立对象。"""
    return [ColumnDef(name, kind, line=1, column=1) for name, kind in (
        ("table_name", "VARCHAR"), ("column_name", "VARCHAR"),
        ("column_type", "VARCHAR"), ("ordinal", "INT"),
        ("source_line", "INT"), ("source_column", "INT"),
    )]


def _valid_name(name: object) -> bool:
    """按冻结词法规则校验直接目录调用中的标识符。"""
    return (isinstance(name, str) and _IDENTIFIER.fullmatch(name) is not None
            and name.upper() not in _KEYWORDS)


def _valid_position(value: object) -> bool:
    """位置必须能被系统目录的 INT 字段保存。"""
    return type(value) is int and 1 <= value <= INT_MAX


class Catalog:
    """加载所选后端；只有成功建表才提交新的表结构。"""

    def __init__(self, json_path: str | None = None, *,
                 storage: StorageEngine | None = None) -> None:
        if json_path is not None and storage is not None:
            raise ValueError("JSON 与页存储后端不能同时指定")
        self.json_path = json_path
        self.storage = storage
        self._tables: dict[str, TableSchema] = {}
        if json_path is not None:
            self._tables = self._load_json()
        elif storage is not None:
            self._tables = self._load_storage()

    def _validate(self, name: str, columns: list[ColumnDef], *,
                  locate_columns: bool = False) -> None:
        """共同预检；直接 API 使用首列位置，语义入口可定位具体列。"""
        first = columns[0] if isinstance(columns, list) and columns else None
        line = getattr(first, "line", 1)
        column = getattr(first, "column", 1)
        line = line if _valid_position(line) else 1
        column = column if _valid_position(column) else 1

        def fail(reason: str, node: ColumnDef | None = None) -> None:
            if locate_columns and node is not None:
                raise SemanticError(node.line, node.column, reason)
            raise SemanticError(line, column, reason)

        if not _valid_name(name) or name.lower() == _SYSTEM_TABLE:
            fail(f"非法或保留的表名：{name}")
        if name.lower() in self._tables:
            fail(f"表已存在：{name}")
        if not isinstance(columns, list) or not columns:
            fail("表必须至少定义一列")
        seen = set()
        for col in columns:
            if not isinstance(col, ColumnDef):
                fail("列定义结构非法")
            if not _valid_position(col.line) or not _valid_position(col.column):
                fail("列定义的源码位置非法")
            if not _valid_name(col.name):
                fail(f"非法或保留的列名：{col.name}", col)
            if col.col_type not in STORAGE_TYPES or type(col.nullable) is not bool:
                fail(f"不支持的列类型：{col.col_type}", col)
            if col.name.lower() in seen:
                fail(f"列重复：{col.name}", col)
            seen.add(col.name.lower())

    def create_table(self, name: str, columns: list[ColumnDef]) -> None:
        """全部校验后持久化，成功后才替换内存目录。"""
        self._validate(name, columns)
        schema: TableSchema = {"name": name, "columns": deepcopy(columns)}
        candidate = {**self._tables, name.lower(): schema}
        if self.json_path is not None:
            self._save_json(candidate)
        elif self.storage is not None:
            storage = self.storage
            storage.create_table(name)
            for ordinal, col in enumerate(schema["columns"]):
                storage.insert_record(_SYSTEM_TABLE, (
                    name, col.name, col.col_type, ordinal, col.line, col.column,
                ), _system_columns())
            storage.flush()
        self._tables = candidate

    def find_table(self, name: str) -> TableSchema | None:
        """返回独立结构，禁止调用方绕过提交修改内部目录。"""
        return deepcopy(self._tables.get(name.lower()))

    def find_column(self, table: str, column: str) -> ColumnDef | None:
        """按大小写不敏感的列名查询。"""
        schema = self._tables.get(table.lower())
        if schema is not None:
            for col in schema["columns"]:
                if col.name.lower() == column.lower():
                    return deepcopy(col)
        return None

    def get_type(self, table: str, column: str) -> str | None:
        """未找到表或列时返回空值。"""
        col = self.find_column(table, column)
        return col.col_type if col is not None else None

    def list_tables(self) -> list[str]:
        """按小写名称排序，保留注册时的原文。"""
        return [self._tables[key]["name"] for key in sorted(self._tables)]

    def snapshot(self) -> Catalog:
        """生成没有文件或存储引用的深复制内存目录。"""
        result = Catalog()
        result._tables = deepcopy(self._tables)
        return result

    def _load_json(self) -> dict[str, TableSchema]:
        """先验证整个文件，任何损坏都不能被视作空目录。"""
        path = Path(self.json_path)
        try:
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                return {}
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if isinstance(data, dict) and data.get("version") == 2:
                data = {key: value for key, value in data.items() if key != "version"}
            elif isinstance(data, dict) and "version" in data:
                raise ValueError("不支持的 JSON 目录版本")
            if not isinstance(data, dict) or set(data) != {"tables"}:
                raise ValueError("目录根结构必须只包含 tables")
            if not isinstance(data["tables"], list):
                raise ValueError("tables 必须为数组")
            temporary = Catalog()
            for table in data["tables"]:
                if not isinstance(table, dict) or set(table) != {"name", "columns"}:
                    raise ValueError("表结构非法")
                if not isinstance(table["columns"], list):
                    raise ValueError("columns 必须为数组")
                columns = []
                for col in table["columns"]:
                    if (not isinstance(col, dict) or set(col) - {"nullable"} != {
                        "node", "name", "col_type", "line", "column"
                    } or col["node"] != "ColumnDef"):
                        raise ValueError("列结构非法")
                    columns.append(ColumnDef(col["name"], col["col_type"],
                                             nullable=col.get("nullable", True),
                                             line=col["line"], column=col["column"]))
                temporary.create_table(table["name"], columns)
            return temporary._tables
        except (OSError, UnicodeError, ValueError, TypeError, SemanticError,
                RecursionError) as exc:
            raise ExecuteError(f"JSON 目录加载失败：{exc}") from exc

    def _save_json(self, tables: dict[str, TableSchema]) -> None:
        """同目录临时文件替换；失败不更新内存，也不覆盖原文件。"""
        path = Path(self.json_path)
        temporary = None
        try:
            data = {"version": 2, "tables": [
                {"name": tables[key]["name"],
                 "columns": [col.to_dict() for col in tables[key]["columns"]]}
                for key in sorted(tables)
            ]}
            content = json.dumps(data, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False) + "\n"
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=path.parent, prefix=f".{path.name}.",
                                             suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
            os.replace(temporary, path)
        except (OSError, ValueError, TypeError) as exc:
            raise ExecuteError(f"JSON 目录提交失败：{exc}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # 清理失败不覆盖原始提交异常。

    def _load_storage(self) -> dict[str, TableSchema]:
        """系统表每列一行，按序号恢复；引导系统表由运行时负责。"""
        storage = self.storage
        if not storage.has_table(_SYSTEM_TABLE):
            raise ExecuteError("系统目录损坏：缺少 __catalog__")
        groups: dict[str, tuple[str, dict[int, ColumnDef]]] = {}
        try:
            for _rid, row in storage.scan_records(_SYSTEM_TABLE, _system_columns()):
                if not isinstance(row, tuple) or len(row) != 6:
                    raise ValueError("目录记录必须为六个字段")
                table, name, kind, ordinal, line, column = row
                if not _valid_name(table) or table.lower() == _SYSTEM_TABLE:
                    raise ValueError("目录表名非法")
                if type(ordinal) is not int or not 0 <= ordinal <= INT_MAX:
                    raise ValueError("列序号非法")
                original, cols = groups.setdefault(table.lower(), (table, {}))
                if table != original or ordinal in cols:
                    raise ValueError("同表名称不一致或列序号重复")
                cols[ordinal] = ColumnDef(name, kind, line=line, column=column)
            temporary = Catalog()
            for original, cols in groups.values():
                if sorted(cols) != list(range(len(cols))):
                    raise ValueError("列序号不连续")
                if not storage.has_table(original):
                    raise ValueError(f"缺少物理表：{original}")
                temporary.create_table(original, [cols[i] for i in range(len(cols))])
            return temporary._tables
        except (ValueError, TypeError, SemanticError) as exc:
            raise ExecuteError(f"系统目录损坏：{exc}") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """拒绝 JSON 对象重复键，避免损坏字段被后值静默覆盖。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段重复：{key}")
        result[key] = value
    return result
