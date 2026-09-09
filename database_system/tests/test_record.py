"""B 负责：记录编解码测试。"""

import pytest

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from storage.record import deserialize, serialize


def test_record_round_trip_uses_little_endian_and_column_order():
    columns = [
        ColumnDef("id", "INT", line=1, column=1),
        ColumnDef("name", "VARCHAR", line=1, column=4),
    ]
    encoded = serialize((7, "A'B"), columns)
    assert encoded == b"\x00\x07\x00\x00\x00\x03\x00A'B"
    assert deserialize(encoded, columns) == (7, "A'B")


@pytest.mark.parametrize("row", [
    (True,),
    (2147483648,),
    ("wrong",),
])
def test_serialize_rejects_invalid_int_values(row):
    columns = [ColumnDef("id", "INT", line=1, column=1)]
    with pytest.raises(ExecuteError):
        serialize(row, columns)


def test_varchar_limit_and_record_limit():
    columns = [ColumnDef("id", "INT", line=1, column=1),
               ColumnDef("text", "VARCHAR", line=1, column=4)]
    assert len(serialize((1, "x" * 255), columns)) == 262
    with pytest.raises(ExecuteError, match="255 bytes"):
        serialize((1, "x" * 256), columns)

    exact_columns = [
        *[ColumnDef(f"v{i}", "VARCHAR", line=1, column=1) for i in range(15)],
        *[ColumnDef(f"i{i}", "INT", line=1, column=1) for i in range(55)],
    ]
    exact_row = ("x" * 255,) * 15 + (1,) * 55
    assert len(serialize(exact_row, exact_columns)) == 4076
    with pytest.raises(ExecuteError, match="record exceeds 4076 bytes"):
        serialize((1,) * 1019,
                  [ColumnDef(f"i{i}", "INT", line=1, column=1)
                   for i in range(1019)])


@pytest.mark.parametrize("data", [b"", b"\x01", b"\x00\x01", b"\x00\x07\x00\x00"])
def test_deserialize_rejects_corrupt_data(data):
    columns = [ColumnDef("id", "INT", line=1, column=1)]
    with pytest.raises(ExecuteError):
        deserialize(data, columns)
