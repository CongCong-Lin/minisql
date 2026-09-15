"""C 的目录后端测试；表级替身只验证契约调用，不冒充 B 的实现。"""

from copy import deepcopy
import json

import pytest

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ExecuteError, SemanticError


def columns():
    return [ColumnDef("Id", "INT", line=3, column=17),
            ColumnDef("Name", "VARCHAR", line=3, column=25)]


class MemoryStorage:
    """内存表级替身，记录调用并在指定阶段注入故障。"""

    def __init__(self):
        self.tables = {"__catalog__"}
        self.rows = []
        self.calls = []
        self.fail = None
        self.flushed_rows = []

    def has_table(self, table):
        return table.lower() in self.tables

    def create_table(self, table):
        self.calls.append(("create", table))
        if self.fail == "create":
            raise ExecuteError("模拟创建故障")
        if table.lower() in self.tables:
            raise ExecuteError("物理表重复")
        if len(self.tables) >= 60:
            raise ExecuteError("table limit exceeded (59 user tables)")
        self.tables.add(table.lower())

    def insert_record(self, table, row, cols):
        assert table == "__catalog__"
        assert [(c.name, c.col_type) for c in cols] == [
            ("table_name", "VARCHAR"), ("column_name", "VARCHAR"),
            ("column_type", "VARCHAR"), ("ordinal", "INT"),
            ("source_line", "INT"), ("source_column", "INT"),
        ]
        assert all((c.line, c.column) == (1, 1) for c in cols)
        self.calls.append(("insert", row))
        if self.fail == "insert":
            raise ExecuteError("模拟目录插入故障")
        self.rows.append(row)
        return (1, len(self.rows) - 1)

    def scan_records(self, table, cols):
        assert table == "__catalog__" and len(cols) == 6
        for index, row in enumerate(self.rows):
            yield ((1, index), row)

    def flush(self):
        self.calls.append(("flush",))
        if self.fail == "flush":
            raise ExecuteError("模拟刷盘故障")
        self.flushed_rows = deepcopy(self.rows)


def test_memory_names_order_and_defensive_copies():
    catalog = Catalog()
    cols = columns()
    catalog.create_table("Zoo", cols)
    catalog.create_table("alpha", columns())
    cols[0].name = "changed"
    schema = catalog.find_table("ZOO")
    assert schema["name"] == "Zoo"
    assert [c.name for c in schema["columns"]] == ["Id", "Name"]
    schema["columns"][0].name = "also_changed"
    col = catalog.find_column("zOo", "ID")
    col.col_type = "VARCHAR"
    assert catalog.get_type("ZOO", "id") == "INT"
    assert catalog.list_tables() == ["alpha", "Zoo"]
    assert catalog.find_table("missing") is None
    assert catalog.find_column("Zoo", "missing") is None
    assert catalog.get_type("missing", "id") is None
    assert catalog.find_table("__catalog__") is None


@pytest.mark.parametrize("name,cols", [
    ("t", []), ("SELECT", columns()), ("__CATALOG__", columns()),
    ("汉字", columns()), ("a" * 65, columns()), ("1table", columns()),
    ("t", [ColumnDef("a", "UNSUPPORTED", line=1, column=1)]),
    ("t", [ColumnDef("a", "INT", line=1, column=1),
           ColumnDef("A", "INT", line=1, column=10)]),
    ("t", [ColumnDef("FROM", "INT", line=1, column=1)]),
    ("t", [ColumnDef("a", "INT", line=0, column=1)]),
    ("t", [ColumnDef("a", "INT", line=True, column=1)]),
    ("t", [ColumnDef("a", "INT", line=2147483648, column=1)]),
])
def test_invalid_schema_does_not_register(name, cols):
    catalog = Catalog()
    with pytest.raises(SemanticError):
        catalog.create_table(name, cols)
    assert catalog.list_tables() == []


def test_duplicate_api_error_uses_first_column_position():
    catalog = Catalog()
    catalog.create_table("Student", columns())
    with pytest.raises(SemanticError) as caught:
        catalog.create_table("STUDENT", columns())
    assert (caught.value.line, caught.value.column) == (3, 17)
    with pytest.raises(SemanticError) as caught:
        catalog.create_table("empty", [])
    assert (caught.value.line, caught.value.column) == (1, 1)


@pytest.mark.parametrize("backend", ["memory", "json", "storage"])
def test_duplicate_column_location_depends_on_entrypoint(tmp_path, backend):
    """直接 API 使用首列位置，SQL 语义入口仍定位重复列。"""
    from sql_compiler.ast_nodes import CreateTableStmt
    from sql_compiler.semantic import analyze

    storage = MemoryStorage()
    path = tmp_path / "catalog.json"
    catalog = (Catalog(str(path)) if backend == "json" else
               Catalog(storage=storage) if backend == "storage" else Catalog())
    cols = [ColumnDef("first", "INT", line=2, column=10),
            ColumnDef("FIRST", "VARCHAR", line=5, column=30)]
    with pytest.raises(SemanticError) as direct:
        catalog.create_table("t", cols)
    assert (direct.value.line, direct.value.column) == (2, 10)
    stmt = CreateTableStmt("t", cols, line=1, column=1)
    with pytest.raises(SemanticError) as semantic:
        analyze([stmt], catalog)
    assert (semantic.value.line, semantic.value.column) == (5, 30)
    assert catalog.list_tables() == []
    assert storage.calls == [] and storage.rows == []
    assert not path.exists()


def test_json_roundtrip_and_stable_format(tmp_path):
    path = tmp_path / "catalog.json"
    catalog = Catalog(str(path))
    assert catalog.list_tables() == [] and not path.exists()
    catalog.create_table("Zoo", columns())
    catalog.create_table("alpha", columns())
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    stored = json.loads(raw)
    assert [table["name"] for table in stored["tables"]] == ["alpha", "Zoo"]
    assert stored["tables"][1]["columns"][0] == columns()[0].to_dict()
    restored = Catalog(str(path))
    assert restored.find_table("zoo") == catalog.find_table("zoo")
    assert restored.list_tables() == ["alpha", "Zoo"]
    assert path.read_bytes() == raw


@pytest.mark.parametrize("backend", ["memory", "json", "storage"])
def test_snapshot_is_detached_and_both_directions_are_isolated(tmp_path, backend):
    storage = MemoryStorage()
    path = tmp_path / "catalog.json"
    catalog = (Catalog(str(path)) if backend == "json" else
               Catalog(storage=storage) if backend == "storage" else Catalog())
    catalog.create_table("t", columns())
    saved = path.read_bytes() if path.exists() else None
    calls = deepcopy(storage.calls)
    snapshot = catalog.snapshot()
    assert snapshot.json_path is snapshot.storage is None
    assert snapshot.find_table("t") == catalog.find_table("t")
    snapshot.create_table("only_snapshot", columns())
    assert catalog.find_table("only_snapshot") is None
    if saved is not None:
        assert path.read_bytes() == saved
    assert storage.calls == calls
    catalog.create_table("only_original", columns())
    assert snapshot.find_table("only_original") is None


def test_zero_length_json_is_empty(tmp_path):
    path = tmp_path / "catalog.json"
    path.touch()
    assert Catalog(str(path)).list_tables() == []
    assert path.stat().st_size == 0


@pytest.mark.parametrize("raw", [
    b" ", b"{", b"[]", b'{"tables":{}}', b'{"tables":[{}]}',
    b'{"tables":[],"tables":[]}', b'{"tables":[],"extra":1}', b"\xff",
])
def test_corrupt_json_is_not_silently_replaced(tmp_path, raw):
    path = tmp_path / "catalog.json"
    path.write_bytes(raw)
    with pytest.raises(ExecuteError):
        Catalog(str(path))
    assert path.read_bytes() == raw


@pytest.mark.parametrize("mutation", ["duplicate_table", "duplicate_column", "type", "position", "node"])
def test_corrupt_json_schema_rejected(tmp_path, mutation):
    table = {"name": "t", "columns": [col.to_dict() for col in columns()]}
    data = {"tables": [table]}
    if mutation == "duplicate_table":
        data["tables"].append(deepcopy(table))
    elif mutation == "duplicate_column":
        table["columns"][1]["name"] = "ID"
    elif mutation == "type":
        table["columns"][0]["col_type"] = "UNSUPPORTED"
    elif mutation == "position":
        table["columns"][0]["line"] = False
    else:
        table["columns"][0]["node"] = "LiteralExpr"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExecuteError):
        Catalog(str(path))


@pytest.mark.parametrize("stage", ["temporary", "replace"])
def test_json_submission_failure_preserves_file_and_memory(tmp_path, monkeypatch, stage):
    import sql_compiler.catalog as module

    path = tmp_path / "catalog.json"
    catalog = Catalog(str(path))
    catalog.create_table("old", columns())
    original = path.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("模拟写入故障")

    if stage == "temporary":
        monkeypatch.setattr(module.tempfile, "NamedTemporaryFile", fail)
    else:
        monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(ExecuteError, match="模拟写入故障"):
        catalog.create_table("new", columns())
    assert path.read_bytes() == original
    assert catalog.list_tables() == ["old"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["catalog.json"]


def test_missing_parent_reports_submission_error(tmp_path):
    catalog = Catalog(str(tmp_path / "missing" / "catalog.json"))
    with pytest.raises(ExecuteError):
        catalog.create_table("t", columns())
    assert catalog.list_tables() == []


def test_page_catalog_roundtrip_preserves_names_positions_and_column_order():
    storage = MemoryStorage()
    catalog = Catalog(storage=storage)
    catalog.create_table("Student", columns())
    assert [call[0] for call in storage.calls] == ["create", "insert", "insert", "flush"]
    assert storage.rows == [
        ("Student", "Id", "INT", 0, 3, 17),
        ("Student", "Name", "VARCHAR", 1, 3, 25),
    ]
    assert storage.flushed_rows == storage.rows
    storage.rows.reverse()
    restored = Catalog(storage=storage)
    assert restored.find_table("STUDENT") == catalog.find_table("student")
    assert restored.list_tables() == ["Student"]
    assert restored.find_table("__catalog__") is None


def test_long_column_names_have_independent_metadata_records():
    storage = MemoryStorage()
    cols = [ColumnDef(letter * 64, "VARCHAR", line=2, column=i + 1)
            for i, letter in enumerate("abc")]
    catalog = Catalog(storage=storage)
    catalog.create_table("T", cols)
    assert len(storage.rows) == 3
    assert all(len(row[1].encode("utf-8")) == 64 for row in storage.rows)
    assert Catalog(storage=storage).find_table("t")["columns"] == cols


@pytest.mark.parametrize("stage", ["create", "insert", "flush"])
def test_storage_failure_never_commits_memory(stage):
    storage = MemoryStorage()
    catalog = Catalog(storage=storage)
    storage.fail = stage
    with pytest.raises(ExecuteError, match="模拟"):
        catalog.create_table("t", columns())
    assert catalog.find_table("t") is None
    assert storage.flushed_rows == []


def test_all_logical_validation_happens_before_physical_creation():
    storage = MemoryStorage()
    catalog = Catalog(storage=storage)
    with pytest.raises(SemanticError):
        catalog.create_table("t", [
            ColumnDef("a", "INT", line=1, column=1),
            ColumnDef("A", "VARCHAR", line=1, column=10)])
    assert storage.calls == []


def test_capacity_failure_is_delegated_to_storage_before_catalog_writes():
    storage = MemoryStorage()
    catalog = Catalog(storage=storage)
    for index in range(59):
        catalog.create_table(f"t{index}", columns())
    old_rows = deepcopy(storage.rows)
    with pytest.raises(ExecuteError, match="table limit exceeded"):
        catalog.create_table("too_many", columns())
    assert storage.rows == old_rows
    assert catalog.find_table("too_many") is None
    assert len(catalog.list_tables()) == 59


def test_memory_catalog_does_not_apply_physical_table_limit():
    catalog = Catalog()
    for index in range(61):
        catalog.create_table(f"t{index}", columns())
    assert len(catalog.list_tables()) == 61


@pytest.mark.parametrize("fault", [
    "missing_system", "missing_table", "gap", "duplicate", "wrong_type",
    "wrong_position", "reserved_table", "inconsistent_name", "bad_shape",
])
def test_corrupt_system_catalog_rejected(fault):
    storage = MemoryStorage()
    storage.tables.add("t")
    storage.rows = [("T", "a", "INT", 0, 1, 1)]
    if fault == "missing_system":
        storage.tables.remove("__catalog__")
    elif fault == "missing_table":
        storage.tables.remove("t")
    elif fault == "gap":
        storage.rows = [("T", "a", "INT", 1, 1, 1)]
    elif fault == "duplicate":
        storage.rows *= 2
    elif fault == "wrong_type":
        storage.rows = [("T", "a", "UNSUPPORTED", 0, 1, 1)]
    elif fault == "wrong_position":
        storage.rows = [("T", "a", "INT", 0, 0, 1)]
    elif fault == "reserved_table":
        storage.rows = [("__catalog__", "a", "INT", 0, 1, 1)]
    elif fault == "inconsistent_name":
        storage.rows.append(("t", "b", "INT", 1, 1, 5))
    else:
        storage.rows = [("T",)]
    with pytest.raises(ExecuteError):
        Catalog(storage=storage)
    assert storage.calls == []


def test_backends_are_mutually_exclusive():
    with pytest.raises(ValueError):
        Catalog("catalog.json", storage=MemoryStorage())
