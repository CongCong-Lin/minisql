"""B 负责：表映射、记录定位、页链和槽操作。"""

from collections.abc import Iterator
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from storage.file_manager import PageStore

RecordId = tuple[int, int]


class StorageEngine:
    """构造仅注入页后端，不读取映射或创建系统表。"""

    def __init__(self, pages: PageStore) -> None:
        """保存页后端，以便使用测试替身独立开发。"""
        self.pages = pages

    def has_table(self, table: str) -> bool:
        """检查物理映射中是否存在指定表。"""
        raise NotImplementedError("M1 存根：表映射由 B 实现")

    def create_table(self, table: str) -> None:
        """检查容量、分配首页并登记物理映射，不操作 Catalog。"""
        raise NotImplementedError("M1 存根：物理建表由 B 实现")

    def insert_record(self, table: str, row: tuple,
                      columns: list[ColumnDef]) -> RecordId:
        """预检记录容量并向表尾追加，返回页号和槽号。"""
        raise NotImplementedError("M1 存根：记录插入由 B 实现")

    def scan_records(self, table: str, columns: list[ColumnDef]
                     ) -> Iterator[tuple[RecordId, tuple]]:
        """按页链和槽号扫描，跳过墓碑并保留记录位置。"""
        raise NotImplementedError("M1 存根：记录扫描由 B 实现")

    def delete_record(self, table: str, rid: RecordId) -> None:
        """按记录位置标记墓碑，不按行值反查或去重。"""
        raise NotImplementedError("M1 存根：记录删除由 B 实现")

    def flush(self) -> None:
        """委托页后端写回全部脏页。"""
        raise NotImplementedError("M1 存根：存储刷盘由 B 实现")

    def close(self) -> None:
        """委托页后端刷盘关闭，完成实现后须支持重复调用。"""
        raise NotImplementedError("M1 存根：存储关闭由 B 实现")
