"""C 负责：JSON、内存与系统目录后端的共同接口。"""

from __future__ import annotations
from typing import TYPE_CHECKING, TypedDict
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError, SemanticError

if TYPE_CHECKING:
    from storage.storage_engine import StorageEngine


class TableSchema(TypedDict):
    """内存表结构，列顺序就是记录存储顺序。"""

    name: str
    columns: list[ColumnDef]


class Catalog:
    """目录构造目前仅保存依赖，不创建文件或装载数据。"""

    def __init__(self, json_path: str | None = None, *,
                 storage: StorageEngine | None = None) -> None:
        """保存互斥后端；无参数表示未来的内存后端。"""
        if json_path is not None and storage is not None:
            raise ValueError("JSON 与页存储后端不能同时指定")
        self.json_path = json_path
        self.storage = storage

    def create_table(self, name: str, columns: list[ColumnDef]) -> None:
        """检查并提交表结构，成功后更新内存目录。"""
        raise NotImplementedError("M1 存根：目录建表由 C 实现")

    def find_table(self, name: str) -> TableSchema | None:
        """按大小写不敏感的名字查找用户表。"""
        raise NotImplementedError("M1 存根：目录查表由 C 实现")

    def find_column(self, table: str, column: str) -> ColumnDef | None:
        """在指定表中查找列。"""
        raise NotImplementedError("M1 存根：目录查列由 C 实现")

    def get_type(self, table: str, column: str) -> str | None:
        """返回列类型或未找到标志。"""
        raise NotImplementedError("M1 存根：目录类型查询由 C 实现")

    def list_tables(self) -> list[str]:
        """返回按小写名字排序的用户表名。"""
        raise NotImplementedError("M1 存根：目录列举由 C 实现")

    def snapshot(self) -> Catalog:
        """返回与真实后端独立的内存目录副本。"""
        raise NotImplementedError("M1 存根：目录快照由 C 实现")
