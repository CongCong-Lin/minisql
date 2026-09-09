"""C 的真实物理页测试；直接操作字节，不实现 B 的记录与表链。"""

import io
import struct

import pytest

from sql_compiler.errors import ExecuteError
from storage.file_manager import PageStore


NULL = 0xFFFFFFFF


@pytest.fixture
def store(tmp_path):
    pages = PageStore(str(tmp_path / "minisql.db"), capacity=2)
    yield pages
    pages.close()


def test_new_database_header_and_empty_file(tmp_path):
    path = tmp_path / "empty.db"
    path.touch()
    pages = PageStore(str(path))
    try:
        assert path.stat().st_size == 4096
        assert struct.unpack_from("<4sHHII", pages.read_page(0)) == (b"MSQL", 1, 0, NULL, 0)
        assert pages.read_page(0)[16:] == bytes(4080)
        assert pages.stats() == {"hits": 0, "misses": 0, "evictions": 0}
    finally:
        pages.close()


@pytest.mark.parametrize("fault", ["alignment", "magic", "version", "reserved", "count"])
def test_bad_file_is_not_reinitialized(tmp_path, fault):
    path = tmp_path / "bad.db"
    data = bytearray(4096)
    struct.pack_into("<4sHHII", data, 0, b"MSQL", 1, 0, NULL, 0)
    if fault == "alignment":
        data.append(0)
    elif fault == "magic":
        data[:4] = b"oops"
    elif fault == "version":
        struct.pack_into("<H", data, 4, 2)
    elif fault == "reserved":
        struct.pack_into("<I", data, 12, 1)
    else:
        struct.pack_into("<H", data, 6, 61)
    path.write_bytes(data)
    with pytest.raises(ExecuteError):
        PageStore(str(path))
    assert path.read_bytes() == data


def test_raw_read_flush_and_direct_write_invalidation(store):
    pid = store.alloc_page()
    page = store.get_page(pid)
    page.data[20] = 7
    page.dirty = True
    assert store.read_page(pid)[20] == 0
    before = store.stats()
    store.flush_page(pid)
    assert not page.dirty and store.read_page(pid)[20] == 7
    assert store.stats() == before
    page.data[20] = 9
    page.dirty = True
    replacement = bytearray(4096)
    replacement[20] = 42
    store.write_page(pid, replacement)
    store.flush_all()
    assert store.read_page(pid)[20] == 42
    fresh = store.get_page(pid)
    assert fresh is not page and fresh.data[20] == 42
    page.data[20] = 99
    store.flush_all()
    assert store.read_page(pid)[20] == 42


@pytest.mark.parametrize("policy,victim", [("LRU", 2), ("FIFO", 1)])
def test_replacement_policy_and_exact_statistics(tmp_path, capsys, policy, victim):
    path = tmp_path / "cache.db"
    pages = PageStore(str(path))
    for _ in range(3):
        pages.alloc_page()
    pages.close()
    pages = PageStore(str(path), capacity=2, policy=policy)
    try:
        first = pages.get_page(1)
        first.data[100] = 17
        first.dirty = True
        pages.get_page(2)
        assert pages.get_page(1) is first
        pages.flush_page(1)
        pages.get_page(3)
        assert pages.stats() == {"hits": 1, "misses": 3, "evictions": 1}
        assert capsys.readouterr().err == f"[BUFFER] evict page={victim}\n"
        assert pages.read_page(1)[100] == 17
    finally:
        pages.close()


def test_capacity_one_dirty_eviction_and_reopen(tmp_path):
    path = tmp_path / "one.db"
    pages = PageStore(str(path), capacity=1)
    for index in range(1, 5):
        pid = pages.alloc_page()
        assert pid == index
        page = pages.get_page(pid)
        page.data[100] = index
        page.dirty = True
    pages.close()
    pages.close()
    restored = PageStore(str(path), capacity=1)
    try:
        for pid in range(1, 5):
            assert restored.get_page(pid).data[100] == pid
    finally:
        restored.close()


@pytest.mark.parametrize("capacity", [1, 3])
def test_free_chain_reuse_and_page_zero_fields_survive_restart(tmp_path, capacity):
    path = tmp_path / "free.db"
    pages = PageStore(str(path), capacity=capacity)
    ids = [pages.alloc_page() for _ in range(3)]
    meta = pages.get_page(0)
    struct.pack_into("<H", meta.data, 6, 1)
    struct.pack_into("<64sI", meta.data, 16, b"t", ids[0])
    meta.dirty = True
    pages.free_page(ids[1])
    pages.free_page(ids[2])
    pages.close()
    pages = PageStore(str(path), capacity=capacity)
    try:
        assert pages.alloc_page() == ids[2]
        assert pages.read_page(ids[2]) == bytes(4096)
        assert pages.alloc_page() == ids[1]
        assert pages.read_page(ids[1]) == bytes(4096)
        assert pages.alloc_page() == 4
        meta = pages.get_page(0)
        assert struct.unpack_from("<H", meta.data, 6)[0] == 1
        assert struct.unpack_from("<64sI", meta.data, 16) == (b"t" + bytes(63), ids[0])
        assert struct.unpack_from("<I", meta.data, 8)[0] == NULL
    finally:
        pages.close()


@pytest.mark.parametrize("pid", [-1, 0, 99, True])
def test_invalid_free_rejected(store, pid):
    with pytest.raises(ExecuteError):
        store.free_page(pid)


def test_double_free_rejected(store):
    pid = store.alloc_page()
    store.free_page(pid)
    with pytest.raises(ExecuteError, match="已经释放"):
        store.free_page(pid)


@pytest.mark.parametrize("fault", ["cycle", "zero", "range", "body"])
def test_corrupt_free_chain_detected_on_reopen(tmp_path, fault):
    path = tmp_path / "broken.db"
    pages = PageStore(str(path))
    pid = pages.alloc_page()
    pages.free_page(pid)
    pages.flush_all()
    raw = bytearray(pages.read_page(pid))
    if fault == "body":
        raw[100] = 1
    else:
        struct.pack_into("<I", raw, 0, {"cycle": pid, "zero": 0, "range": 100}[fault])
    pages.write_page(pid, raw)
    pages.close()
    with pytest.raises(ExecuteError):
        PageStore(str(path))


def test_debug_write_does_not_leave_stale_free_chain_state(store):
    first, second = store.alloc_page(), store.alloc_page()
    store.free_page(first)
    store.free_page(second)
    data = bytearray(4096)
    struct.pack_into("<I", data, 0, NULL)
    store.write_page(second, data)
    assert store.alloc_page() == second
    assert store.alloc_page() == 3


def test_invalid_access_and_lengths_do_not_change_statistics(store):
    before = store.stats()
    for method in (store.get_page, store.read_page, store.flush_page):
        for pid in (-1, 100, True):
            with pytest.raises(ExecuteError):
                method(pid)
    for length in (0, 4095, 4097):
        with pytest.raises(ExecuteError):
            store.write_page(0, bytes(length))
    assert store.stats() == before
    store.flush_page(0)


def test_external_resize_cannot_corrupt_file(store):
    page = store.get_page(0)
    page.data.append(0)
    page.dirty = True
    with pytest.raises(ExecuteError, match="4096"):
        store.flush_all()
    assert page.dirty
    page.data.pop()
    store.flush_all()


class BrokenFile:
    """文件故障代理，不影响真实句柄在测试后被关闭。"""

    def __init__(self, wrapped, fault):
        self.wrapped = wrapped
        self.fault = fault

    def seek(self, *args):
        return self.wrapped.seek(*args)

    def read(self, size):
        if self.fault == "read":
            raise OSError("模拟读取故障")
        result = self.wrapped.read(size)
        return result[:-1] if self.fault == "short_read" else result

    def write(self, data):
        if self.fault == "write":
            raise OSError("模拟写入故障")
        if self.fault == "short_write":
            return self.wrapped.write(data[:-1])
        return self.wrapped.write(data)

    def close(self):
        self.wrapped.close()
        if self.fault == "close":
            raise OSError("模拟关闭故障")


@pytest.mark.parametrize("fault", ["read", "short_read"])
def test_read_failures_are_execute_errors(store, fault):
    wrapped = store._file
    store._file = BrokenFile(wrapped, fault)
    try:
        with pytest.raises(ExecuteError):
            store.get_page(0)
        assert store.stats()["misses"] == 1
    finally:
        store._file = wrapped


@pytest.mark.parametrize("fault", ["write", "short_write"])
def test_failed_writeback_keeps_dirty_state(store, fault):
    page = store.get_page(0)
    page.dirty = True
    wrapped = store._file
    store._file = BrokenFile(wrapped, fault)
    try:
        with pytest.raises(ExecuteError):
            store.flush_all()
        assert page.dirty
    finally:
        store._file = wrapped


def test_failed_eviction_preserves_old_page_and_no_success_log(tmp_path, capsys):
    pages = PageStore(str(tmp_path / "fail.db"), capacity=1)
    pid = pages.alloc_page()
    old = pages.get_page(0)
    old.dirty = True
    capsys.readouterr()
    before = pages.stats()
    wrapped = pages._file
    pages._file = BrokenFile(wrapped, "write")
    try:
        with pytest.raises(ExecuteError):
            pages.get_page(pid)
        assert pages.get_page(0) is old and old.dirty
        assert pages.stats()["evictions"] == before["evictions"]
        assert capsys.readouterr().err == ""
    finally:
        pages._file = wrapped
        pages.close()


@pytest.mark.parametrize("fault", ["write", "broken_pipe", "closed"])
def test_eviction_log_failure_preserves_completed_replacement(
        tmp_path, capsys, monkeypatch, fault):
    """日志故障必须上报，同时保留已经完成的刷盘和缓存替换。"""
    pages = PageStore(str(tmp_path / "log-failure.db"), capacity=1)
    pid = pages.alloc_page()
    old = pages.get_page(0)
    old.data[100] = 17
    old.dirty = True
    before = pages.stats()
    capsys.readouterr()
    cause = (BrokenPipeError("模拟日志管道断开") if fault == "broken_pipe"
             else OSError("模拟日志写入故障"))

    class BrokenLog:
        """模拟标准错误输出故障，不接触数据库文件。"""

        def write(self, text):
            raise cause

    log = BrokenLog()
    if fault == "closed":
        log = io.StringIO()
        log.close()
    try:
        with monkeypatch.context() as patch:
            patch.setattr("storage.buffer.sys.stderr", log)
            with pytest.raises(ExecuteError, match="缓存淘汰日志") as caught:
                pages.get_page(pid)
            if fault == "closed":
                assert isinstance(caught.value.__cause__, ValueError)
            else:
                assert caught.value.__cause__ is cause
            assert not old.dirty and pages.read_page(0)[100] == 17
            assert list(pages.buffer._pages) == [pid]
            assert pages.stats() == {
                "hits": before["hits"],
                "misses": before["misses"] + 1,
                "evictions": before["evictions"] + 1,
            }
            fresh = pages.buffer._pages[pid]
            assert pages.get_page(pid) is fresh
            assert bytes(fresh.data) == bytes(4096)
            assert pages.stats()["hits"] == before["hits"] + 1
        assert pages.get_page(pid) is fresh
        assert capsys.readouterr().err == ""
        assert pages.get_page(0).data[100] == 17
        assert capsys.readouterr().err == f"[BUFFER] evict page={pid}\n"
        assert pages.stats() == {
            "hits": before["hits"] + 2,
            "misses": before["misses"] + 2,
            "evictions": before["evictions"] + 2,
        }
    finally:
        pages.close()


@pytest.mark.parametrize("fault", ["write", "close"])
def test_close_failure_releases_handle_and_propagates(tmp_path, fault):
    pages = PageStore(str(tmp_path / "close.db"))
    page = pages.get_page(0)
    page.dirty = True
    wrapped = pages._file
    pages._file = BrokenFile(wrapped, fault)
    with pytest.raises(ExecuteError):
        pages.close()
    assert wrapped.closed
    if fault == "write":
        assert page.dirty
    pages.close()


def test_closed_store_allows_only_stats_and_repeated_close(tmp_path):
    pages = PageStore(str(tmp_path / "closed.db"))
    pages.close()
    pages.close()
    assert pages.stats() == {"hits": 0, "misses": 0, "evictions": 0}
    for action in (lambda: pages.read_page(0), lambda: pages.get_page(0),
                   lambda: pages.write_page(0, bytes(4096)),
                   lambda: pages.flush_page(0), pages.alloc_page,
                   lambda: pages.free_page(1), pages.flush_all):
        with pytest.raises(ExecuteError, match="关闭"):
            action()


def test_missing_parent_is_reported(tmp_path):
    with pytest.raises(ExecuteError):
        PageStore(str(tmp_path / "missing" / "db"))


@pytest.mark.parametrize("capacity,policy", [(0, "LRU"), (True, "LRU"), (1, "other")])
def test_invalid_configuration_does_not_create_file(tmp_path, capacity, policy):
    path = tmp_path / "db"
    with pytest.raises(ValueError):
        PageStore(str(path), capacity=capacity, policy=policy)
    assert not path.exists()
