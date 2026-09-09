"""B 负责：表级存储引擎测试。"""

import struct

import pytest

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from storage.page import Page
from storage.storage_engine import StorageEngine


class FakePages:
    def __init__(self):
        self.pages = {0: Page(0)}
        struct.pack_into("<4sHHII", self.pages[0].data, 0,
                         b"MSQL", 1, 0, 0xFFFFFFFF, 0)
        self.next_id = 1

    def get_page(self, page_id):
        return self.pages[page_id]

    def alloc_page(self):
        page_id = self.next_id
        self.next_id += 1
        self.pages[page_id] = Page(page_id)
        return page_id

    def flush_all(self):
        pass

    def close(self):
        pass


class CapacityOnePages(FakePages):
    """模拟容量为 1 的页缓存，访问新页会使旧页失效。"""

    def __init__(self):
        super().__init__()
        self.cached = None

    def get_page(self, page_id):
        if self.cached is not None and self.cached.id != page_id:
            if self.cached.dirty:
                self.pages[self.cached.id].data[:] = self.cached.data
                self.pages[self.cached.id].dirty = False
        if self.cached is None or self.cached.id != page_id:
            disk_page = self.pages[page_id]
            self.cached = Page(page_id, bytearray(disk_page.data), disk_page.dirty)
        return self.cached


@pytest.fixture
def storage():
    return StorageEngine(FakePages())


@pytest.fixture
def columns():
    return [ColumnDef("id", "INT", line=1, column=1),
            ColumnDef("name", "VARCHAR", line=1, column=4)]


def test_table_mapping_and_cross_page_scan(storage, columns):
    storage.create_table("People")
    assert storage.has_table("people")
    rids = [storage.insert_record("PEOPLE", (i, "x" * 250), columns)
            for i in range(40)]

    rows = list(storage.scan_records("people", columns))
    assert [row for _, row in rows] == [(i, "x" * 250) for i in range(40)]
    assert len({rid[0] for rid in rids}) > 1


def test_delete_marks_tombstone_without_reusing_slot(storage, columns):
    storage.create_table("t")
    first = storage.insert_record("t", (1, "a"), columns)
    second = storage.insert_record("t", (2, "b"), columns)
    storage.delete_record("t", first)
    assert list(storage.scan_records("t", columns)) == [(second, (2, "b"))]
    storage.delete_record("t", first)
    third = storage.insert_record("t", (3, "c"), columns)
    assert third[1] > first[1]


def test_invalid_record_id_is_rejected(storage, columns):
    storage.create_table("t")
    storage.insert_record("t", (1, "a"), columns)
    with pytest.raises(ExecuteError):
        storage.delete_record("t", (999, 0))
    with pytest.raises(ExecuteError):
        storage.delete_record("t", (1, -1))
    with pytest.raises(ExecuteError):
        storage.delete_record("t", [1, 0])


def test_metadata_has_expected_mapping_and_page_chain(storage, columns):
    storage.create_table("t")
    for i in range(40):
        storage.insert_record("t", (i, "x" * 250), columns)
    metadata = storage.pages.pages[0]
    _, version, count, _, reserved = struct.unpack_from("<4sHHII", metadata.data)
    assert version == 1 and count == 1 and reserved == 0
    first_page = struct.unpack_from("<I", metadata.data, 16 + 64)[0]
    first = storage.pages.pages[first_page]
    first_id, _, _, prev, next_page = struct.unpack_from("<IHHII", first.data)
    assert first_id == first_page and prev == 0xFFFFFFFF and next_page != 0xFFFFFFFF


def test_cross_page_append_keeps_chain_when_cache_capacity_is_one(columns):
    pages = CapacityOnePages()
    storage = StorageEngine(pages)
    storage.create_table("t")
    for i in range(40):
        storage.insert_record("t", (i, "x" * 250), columns)

    rows = list(storage.scan_records("t", columns))
    assert len(rows) == 40
    assert [row for _, row in rows] == [(i, "x" * 250) for i in range(40)]
