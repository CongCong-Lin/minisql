"""B 负责：按固定列序进行记录编解码。"""

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError


def serialize(record: tuple, columns: list[ColumnDef]) -> bytes:
    """输出保留头、INT 和 VARCHAR 组成的完整记录。"""
    raise NotImplementedError("M1 存根：记录序列化由 B 实现")


def deserialize(data: bytes, columns: list[ColumnDef]) -> tuple:
    """校验记录字节并按列序还原元组，墓碑由扫描器处理。"""
    raise NotImplementedError("M1 存根：记录反序列化由 B 实现")
