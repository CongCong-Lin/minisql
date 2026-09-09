"""B 负责：按固定列序进行记录编解码。"""

import struct
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError


def serialize(record: tuple, columns: list[ColumnDef]) -> bytes:
    """输出保留头、INT 和 VARCHAR 组成的完整记录。"""
    if len(record) != len(columns):
        raise ExecuteError("record column count mismatch")
    out = bytearray(b"\x00")
    for value, column in zip(record, columns):
        if column.col_type == "INT":
            if isinstance(value, bool) or not isinstance(value, int) or not -2147483648 <= value <= 2147483647:
                raise ExecuteError("invalid INT value")
            out.extend(struct.pack("<i", value))
        elif column.col_type == "VARCHAR":
            if not isinstance(value, str):
                raise ExecuteError("invalid VARCHAR value")
            try:
                encoded = value.encode("utf-8")
            except UnicodeEncodeError:
                raise ExecuteError("invalid VARCHAR value")
            if len(encoded) > 255:
                raise ExecuteError("VARCHAR value exceeds 255 bytes")
            out.extend(struct.pack("<H", len(encoded))); out.extend(encoded)
        else:
            raise ExecuteError("unsupported column type")
    if len(out) > 4076:
        raise ExecuteError("record exceeds 4076 bytes")
    return bytes(out)


def deserialize(data: bytes, columns: list[ColumnDef]) -> tuple:
    """校验记录字节并按列序还原元组，墓碑由扫描器处理。"""
    if len(data) < 1 or len(data) > 4076 or data[0] != 0:
        raise ExecuteError("invalid record header")
    pos, values = 1, []
    try:
        for column in columns:
            if column.col_type == "INT":
                if pos + 4 > len(data): raise ValueError
                values.append(struct.unpack_from("<i", data, pos)[0]); pos += 4
            elif column.col_type == "VARCHAR":
                if pos + 2 > len(data): raise ValueError
                length = struct.unpack_from("<H", data, pos)[0]; pos += 2
                if pos + length > len(data): raise ValueError
                values.append(data[pos:pos + length].decode("utf-8")); pos += length
            else: raise ValueError
    except (ValueError, UnicodeDecodeError, struct.error):
        raise ExecuteError("invalid record data")
    if pos != len(data):
        raise ExecuteError("invalid record data")
    return tuple(values)
