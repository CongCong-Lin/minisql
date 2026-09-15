"""按列定义编解码记录；非空旧记录保持原始紧凑布局。"""
from datetime import date, timedelta
import math
import struct
from sql_compiler.errors import ExecuteError

EPOCH = date(1970, 1, 1)


def serialize(record, columns):
    """仅在含空值时附加位图，所有类型共用记录容量检查。"""
    if len(record) != len(columns):
        raise ExecuteError("record column count mismatch")
    has_null = any(value is None for value in record)
    bitmap = bytearray((len(columns) + 7) // 8) if has_null else bytearray()
    payload = bytearray()
    try:
        for index, (value, column) in enumerate(zip(record, columns)):
            if value is None:
                if not column.nullable:
                    raise ExecuteError(f"列 {column.name} 不允许 NULL")
                bitmap[index // 8] |= 1 << (index % 8)
                continue
            kind = column.col_type
            if kind == "INT":
                if type(value) is not int or not -(1 << 31) <= value < 1 << 31:
                    raise ExecuteError("invalid INT value")
                payload.extend(struct.pack("<i", value))
            elif kind == "VARCHAR":
                if not isinstance(value, str):
                    raise ExecuteError("invalid VARCHAR value")
                encoded = value.encode("utf-8")
                if len(encoded) > 255:
                    raise ExecuteError("VARCHAR value exceeds 255 bytes")
                payload.extend(struct.pack("<H", len(encoded)))
                payload.extend(encoded)
            elif kind == "FLOAT":
                if type(value) not in {int, float} or not math.isfinite(value):
                    raise ExecuteError("FLOAT 必须为有限数值")
                payload.extend(struct.pack("<d", float(value)))
            elif kind == "BOOL":
                if type(value) is not bool:
                    raise ExecuteError("BOOL 必须为布尔值")
                payload.append(int(value))
            elif kind == "DATE":
                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                    raise ExecuteError("DATE 必须为有效的 YYYY-MM-DD")
                payload.extend(struct.pack("<i", (date.fromisoformat(value) - EPOCH).days))
            else:
                raise ExecuteError("unsupported column type")
    except (ValueError, UnicodeError, OverflowError, struct.error) as exc:
        raise ExecuteError("记录字段值非法") from exc
    result = bytes([int(has_null)]) + bitmap + payload
    if len(result) > 4076:
        raise ExecuteError("record exceeds 4076 bytes")
    return bytes(result)


def deserialize(data, columns):
    """拒绝非法标记、非有限浮点、字符串超限及尾部多余字节。"""
    if not 1 <= len(data) <= 4076 or data[0] not in {0, 1}:
        raise ExecuteError("invalid record header")
    size = (len(columns) + 7) // 8 if data[0] else 0
    bitmap = data[1:1 + size]
    pos, values = 1 + size, []
    try:
        if len(bitmap) != size or size and len(columns) % 8 and bitmap[-1] >> (len(columns) % 8):
            raise ValueError
        for index, column in enumerate(columns):
            if size and bitmap[index // 8] & (1 << (index % 8)):
                if not column.nullable:
                    raise ValueError
                values.append(None)
                continue
            kind = column.col_type
            if kind in {"INT", "DATE"}:
                value = struct.unpack_from("<i", data, pos)[0]
                pos += 4
                if kind == "DATE":
                    value = (EPOCH + timedelta(days=value)).isoformat()
            elif kind == "FLOAT":
                value = struct.unpack_from("<d", data, pos)[0]
                pos += 8
                if not math.isfinite(value):
                    raise ValueError
            elif kind == "BOOL":
                value = data[pos]
                pos += 1
                if value not in {0, 1}:
                    raise ValueError
                value = bool(value)
            elif kind == "VARCHAR":
                length = struct.unpack_from("<H", data, pos)[0]
                pos += 2
                if length > 255 or pos + length > len(data):
                    raise ValueError
                value = data[pos:pos + length].decode("utf-8")
                pos += length
            else:
                raise ValueError
            values.append(value)
        if pos != len(data):
            raise ValueError
    except (ValueError, UnicodeError, struct.error, IndexError, OverflowError) as exc:
        raise ExecuteError("invalid record data") from exc
    return tuple(values)
