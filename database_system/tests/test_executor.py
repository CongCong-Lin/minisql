"""D 负责：执行算子、列重排、删除安全性和刷盘。"""

import pytest

from engine.executor import execute
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from tests.d_support import (
    FakeCatalog, FakeStorage, binary, ensure_arithmetic, ident, lit,
)


@pytest.fixture(autouse=True)
def _arithmetic(monkeypatch):
    ensure_arithmetic(monkeypatch)


def _student_catalog() -> FakeCatalog:
    catalog = FakeCatalog()
    catalog.create_table("Student", [
        ColumnDef("id", "INT", line=1, column=16),
        ColumnDef("name", "VARCHAR", line=1, column=24),
    ])
    catalog.create_calls = 0
    return catalog


def test_create_table_calls_catalog_once_and_flushes():
    """CreateTable 只登记一次，成功路径必须刷盘。"""
    catalog = FakeCatalog()
    storage = FakeStorage()
    catalog.storage = storage
    plan = {
        "op": "CreateTable",
        "table": "student",
        "columns": [{"name": "id", "type": "INT", "line": 1, "column": 22}],
    }
    result = execute(plan, catalog, storage)
    assert result.message == "OK"
    assert result.rows == [] and result.columns == []
    assert catalog.create_calls == 1
    assert storage.has_table("student")
    assert storage.flushed == 1


def test_insert_reorders_values_to_catalog_order():
    """INSERT 目标列允许重排，物理行必须按 Catalog 列序。"""
    catalog = _student_catalog()
    storage = FakeStorage()
    storage.create_table("Student")
    plan = {
        "op": "Insert",
        "table": "Student",
        "columns": ["name", "id"],
        "values": ["Alice", 1],
    }
    result = execute(plan, catalog, storage)
    assert result.message == "1 row inserted"
    rows = list(storage.scan_records("Student", catalog.find_table("Student")["columns"]))
    assert rows[0][1] == (1, "Alice")


def test_select_star_headers_use_catalog_names():
    """SELECT * 表头使用 Catalog 原文，空表仍有表头。"""
    catalog = _student_catalog()
    storage = FakeStorage()
    storage.create_table("Student")
    plan = {
        "op": "Project",
        "columns": "*",
        "child": {"op": "SeqScan", "table": "Student"},
    }
    result = execute(plan, catalog, storage)
    assert result.columns == ["id", "name"]
    assert result.rows == []
    assert result.message == "0 rows selected"


def test_select_projects_catalog_header_case():
    """指定列按 SELECT 顺序输出，表头取 Catalog 保存的大小写。"""
    catalog = _student_catalog()
    storage = FakeStorage()
    storage.create_table("Student")
    storage.insert_record("Student", (1, "Ada"), catalog.find_table("Student")["columns"])
    plan = {
        "op": "Project",
        "columns": ["NAME"],
        "child": {"op": "SeqScan", "table": "Student"},
    }
    result = execute(plan, catalog, storage)
    assert result.columns == ["name"]
    assert result.rows == [("Ada",)]
    assert result.message == "1 row selected"


def test_delete_waits_until_all_predicates_succeed():
    """谓词求值失败时不得标记任何记录。"""
    catalog = _student_catalog()
    storage = FakeStorage()
    storage.create_table("Student")
    columns = catalog.find_table("Student")["columns"]
    storage.insert_record("Student", (1, "Ada"), columns)
    storage.insert_record("Student", (2, "Bob"), columns)
    plan = {
        "op": "Delete",
        "table": "Student",
        "child": {
            "op": "Filter",
            "predicate": binary(
                "AND",
                binary("=", ident("id", 1, 32), lit(1, "INT", 1, 35), 1, 33),
                binary("/", lit(1, "INT", 1, 40), lit(0, "INT", 1, 42), 1, 41),
                1, 37,
            ),
            "child": {"op": "SeqScan", "table": "Student"},
        },
    }
    with pytest.raises(ExecuteError, match="division by zero"):
        execute(plan, catalog, storage)
    alive = list(storage.scan_records("Student", columns))
    assert len(alive) == 2
    assert storage.flushed == 0


def test_delete_counts_and_flushes():
    """DELETE 先筛后删，成功后刷盘并使用单复数消息。"""
    catalog = _student_catalog()
    storage = FakeStorage()
    storage.create_table("Student")
    columns = catalog.find_table("Student")["columns"]
    storage.insert_record("Student", (1, "Ada"), columns)
    storage.insert_record("Student", (1, "Ada"), columns)
    plan = {
        "op": "Delete",
        "table": "Student",
        "child": {
            "op": "Filter",
            "predicate": binary("=", ident("id", 1, 32), lit(1, "INT", 1, 35), 1, 33),
            "child": {"op": "SeqScan", "table": "Student"},
        },
    }
    result = execute(plan, catalog, storage)
    assert result.message == "2 rows deleted"
    assert list(storage.scan_records("Student", columns)) == []
    assert storage.flushed == 1


def test_empty_table_does_not_evaluate_constant_predicate():
    """空表不求值 WHERE，即使谓词含除零。"""
    catalog = _student_catalog()
    storage = FakeStorage()
    storage.create_table("Student")
    plan = {
        "op": "Project",
        "columns": "*",
        "child": {
            "op": "Filter",
            "predicate": binary("/", lit(1, "INT", 1, 20), lit(0, "INT", 1, 22), 1, 21),
            "child": {"op": "SeqScan", "table": "Student"},
        },
    }
    result = execute(plan, catalog, storage)
    assert result.rows == []
    assert result.message == "0 rows selected"


def test_unknown_operator_is_execute_error():
    """未知算子必须失败，不能当成空成功。"""
    with pytest.raises(ExecuteError, match="unsupported plan operator"):
        execute({"op": "Join"}, _student_catalog(), FakeStorage())
