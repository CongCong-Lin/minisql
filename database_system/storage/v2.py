"""第二版页存储与堆表；目录自身存放于编号一的系统堆表。"""

import json
import os
import struct
import uuid

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ExecuteError
from storage.buffer import BufferPool
from storage.file_manager import PageStore
from storage.page import PAGE_SIZE
from storage.record import serialize, deserialize

END = 0xFFFFFFFF
HEAP = 1 << 30
SUPER = struct.Struct("<4sHH16sIII")
CHUNKS = [ColumnDef("part", "VARCHAR", line=1, column=1)]


class V2PageStore(PageStore):
    """复用缓存接口，所有物理写入先经过撤销日志。"""

    def __init__(self, stream, journal, capacity=64, policy="LRU"):
        self._file = stream
        self.path = stream.name
        self.journal = journal
        self.buffer = BufferPool(capacity, policy)
        self._closed = False
        self._page_count = os.fstat(stream.fileno()).st_size // PAGE_SIZE
        self.bootstrap = self._page_count == 0
        self.writable = True
        if self._page_count == 0:
            data = bytearray(PAGE_SIZE)
            SUPER.pack_into(data, 0, b"MSQL", 2, PAGE_SIZE, uuid.uuid4().bytes, 1, END, 2)
            self._write_disk(0, data, append=True)
            self._page_count = 1
            self.alloc_page()
            self.init_heap(1, 1)
            self.flush_all()
        self.refresh()

    def refresh(self):
        size = os.fstat(self._file.fileno()).st_size
        if not size or size % PAGE_SIZE:
            raise ExecuteError("数据库文件长度非法")
        self.buffer = BufferPool(self.buffer.capacity, self.buffer.policy)
        self._page_count = size // PAGE_SIZE
        head = self._read_disk(0)
        magic, version, size, _, root, _, next_id = SUPER.unpack_from(head)
        if magic != b"MSQL" or version != 2:
            raise ExecuteError("数据库版本不受支持，请使用 tools.migrate 迁移旧库")
        if size != PAGE_SIZE or root != 1 or not 2 <= next_id <= (1 << 30):
            raise ExecuteError("数据库超级块损坏")
        self._walk_free_chain()

    def _write_disk(self, page_id, data, *, append=False):
        if not self.writable or self.journal.stream is None:
            raise ExecuteError("物理写入必须位于写事务内")
        self.journal.before_write(self._file, page_id)
        super()._write_disk(page_id, data, append=append)
        self.journal._event("数据页已写入")

    def super_value(self, offset, value=None):
        page = self.get_page(0)
        if value is not None:
            if not self.writable:
                raise ExecuteError("只读事务不能修改超级块")
            struct.pack_into("<I", page.data, offset, value)
            page.dirty = True
        return struct.unpack_from("<I", page.data, offset)[0]

    def new_object(self):
        number = self.super_value(32)
        if number >= 1 << 30:
            raise ExecuteError("对象编号已耗尽")
        self.super_value(32, number + 1)
        return number

    def _walk_free_chain(self, **kwargs):
        current = self.super_value(28)
        seen = set()
        while current != END:
            if current in seen or current <= 1:
                raise ExecuteError("空闲页链损坏")
            seen.add(current)
            data = self._read_disk(current)
            if any(data[:4]) or any(data[8:]):
                raise ExecuteError("空闲页格式损坏")
            current = struct.unpack_from("<I", data, 4)[0]
        return seen

    def alloc_page(self):
        first = self.super_value(28)
        if first != END:
            data = self.get_page(first).data
            if any(data[:4]) or any(data[8:]):
                raise ExecuteError("空闲页格式损坏")
            following = struct.unpack_from("<I", data, 4)[0]
            self.super_value(28, following)
            self.write_page(first, bytes(PAGE_SIZE))
            return first
        page_id = self._page_count
        self._write_disk(page_id, bytes(PAGE_SIZE), append=True)
        self._page_count += 1
        return page_id

    def free_page(self, page_id):
        self._check_page(page_id)
        if page_id <= 1 or struct.unpack_from("<I", self.get_page(page_id).data)[0] == 0:
            raise ExecuteError("不能释放系统入口或重复释放页")
        data = bytearray(PAGE_SIZE)
        struct.pack_into("<I", data, 4, self.super_value(28))
        self.write_page(page_id, data)
        self.super_value(28, page_id)

    def init_heap(self, page_id, owner, previous=END):
        page = self.get_page(page_id)
        page.data[:] = bytes(PAGE_SIZE)
        struct.pack_into("<IHHII", page.data, 0, HEAP | owner, 0, 16, previous, END)
        page.dirty = True

    def close(self):
        # 会话统一提交或回滚；关闭句柄绝不隐式刷盘。
        if not self._closed:
            self._closed = True
            self._file.close()


class V2Storage:
    """表数据、索引和目录共享同一物理文件及日志。"""

    def __init__(self, pages):
        self.pages = pages
        self.meta = {"tables": {}, "indexes": {}, "security": None}
        self.catalog = None
        self.reload()

    def reload(self):
        chunks = list(self._scan({"id": 1, "root": 1}, CHUNKS))
        if not chunks and not self.pages.bootstrap:
            raise ExecuteError("系统目录为空或损坏")
        if chunks:
            try:
                meta = json.loads("".join(row[0] for _, row in chunks))
                if not isinstance(meta, dict) or set(meta) != {"tables", "indexes", "security"}:
                    raise ValueError
                if not isinstance(meta["tables"], dict) or not isinstance(meta["indexes"], dict):
                    raise ValueError
                identifiers = {1}
                for name, entry in meta["tables"].items():
                    if name != entry["name"].lower() or not entry["columns"]:
                        raise ValueError
                    for key in ("id", "root", "tail", "pages", "rows", "version"):
                        if type(entry[key]) is not int or entry[key] < (2 if key in {"id", "root", "tail"} else 0):
                            raise ValueError
                    if entry["id"] in identifiers or entry["id"] >= self.pages.super_value(32):
                        raise ValueError
                    identifiers.add(entry["id"])
                    if not isinstance(entry["stats"], dict):
                        raise ValueError
                    for column in entry["columns"]:
                        if not isinstance(column["name"], str) or column["type"] not in {"INT", "VARCHAR", "FLOAT", "BOOL", "DATE"} or type(column["nullable"]) is not bool:
                            raise ValueError
                        if type(column["line"]) is not int or type(column["column"]) is not int:
                            raise ValueError
                for name, spec in meta["indexes"].items():
                    if name != spec["name"].lower() or spec["table"] not in meta["tables"]:
                        raise ValueError
                    columns = meta["tables"][spec["table"]]["columns"]
                    if type(spec["column"]) is not int or not 0 <= spec["column"] < len(columns):
                        raise ValueError
                    if spec["type"] != columns[spec["column"]]["type"]:
                        raise ValueError
                    for key in ("id", "root", "pages", "leaves", "height", "entries"):
                        if type(spec[key]) is not int or spec[key] < (0 if key == "entries" else 1):
                            raise ValueError
                    if spec["id"] in identifiers or spec["id"] >= self.pages.super_value(32):
                        raise ValueError
                    identifiers.add(spec["id"])
                security = meta["security"]
                if security is not None and (not isinstance(security, dict) or not isinstance(security["users"], dict) or not isinstance(security["roles"], list) or not isinstance(security["grants"], list)):
                    raise ValueError
                self.meta = meta
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ExecuteError("系统目录损坏") from exc

    def save_metadata(self):
        encoded = json.dumps(self.meta, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        chain = list(self._chain({"id": 1, "root": 1}))
        for page_id in chain[1:]:
            self.pages.free_page(page_id)
        self.pages.init_heap(1, 1)
        entry = {"id": 1, "root": 1, "tail": 1, "pages": 1}
        for start in range(0, len(encoded), 255):
            self._insert(entry, serialize((encoded[start:start + 255],), CHUNKS))
        self.pages.bootstrap = False

    def _entry(self, table):
        entry = self.meta["tables"].get(table.lower())
        if entry is None:
            raise ExecuteError("表不存在")
        return entry

    def has_table(self, table):
        return table.lower() in self.meta["tables"]

    def create_table(self, table, columns):
        if self.has_table(table):
            raise ExecuteError("表已存在")
        owner = self.pages.new_object()
        root = self.pages.alloc_page()
        self.pages.init_heap(root, owner)
        self.meta["tables"][table.lower()] = {
            "name": table, "id": owner, "root": root, "tail": root, "pages": 1,
            "rows": 0, "version": 0, "owner": None, "stats": {},
            "columns": [{"name": c.name, "type": c.col_type, "nullable": c.nullable,
                         "line": c.line, "column": c.column} for c in columns],
        }

    def _header(self, page_id, owner):
        page = self.pages.get_page(page_id)
        header = struct.unpack_from("<IHHII", page.data)
        if (header[0] != HEAP | owner or not 16 <= header[2] <= PAGE_SIZE - header[1] * 4
                or header[3] == 0 or header[4] == 0):
            raise ExecuteError("数据页类型、归属或边界损坏")
        return header

    def _chain(self, entry):
        current, previous, seen = entry["root"], END, set()
        while current != END:
            if current in seen:
                raise ExecuteError("数据页链循环")
            seen.add(current)
            _, _, _, prev, nxt = self._header(current, entry["id"])
            if prev != previous:
                raise ExecuteError("数据页链前向指针错误")
            yield current
            previous, current = current, nxt

    def _slot(self, entry, rid, allow_deleted=False):
        if not isinstance(rid, tuple) or len(rid) != 2 or any(type(x) is not int or x < 0 for x in rid):
            raise ExecuteError("记录位置非法")
        page_id, slot = rid
        _, count, free, _, _ = self._header(page_id, entry["id"])
        if slot >= count:
            raise ExecuteError("记录槽不存在")
        page = self.pages.get_page(page_id)
        offset, length = struct.unpack_from("<HH", page.data, PAGE_SIZE - 4 * (slot + 1))
        if offset == 0xFFFF and allow_deleted:
            return None
        if offset < 16 or length < 1 or offset + length > free:
            raise ExecuteError("记录槽已删除或边界损坏")
        return offset, length

    def _scan(self, entry, columns):
        for page_id in self._chain(entry):
            count = self._header(page_id, entry["id"])[1]
            for slot in range(count):
                rid = (page_id, slot)
                location = self._slot(entry, rid, True)
                if location is not None:
                    offset, length = location
                    data = bytes(self.pages.get_page(page_id).data[offset:offset + length])
                    yield rid, deserialize(data, columns)

    def scan_records(self, table, columns):
        yield from self._scan(self._entry(table), columns)

    def get_record(self, table, rid, columns):
        offset, length = self._slot(self._entry(table), rid)
        return deserialize(bytes(self.pages.get_page(rid[0]).data[offset:offset + length]), columns)

    def _insert(self, entry, data):
        page_id = entry["tail"]
        _, slots, free, _, _ = self._header(page_id, entry["id"])
        if free + len(data) > PAGE_SIZE - 4 * (slots + 1):
            new_id = self.pages.alloc_page()
            page = self.pages.get_page(page_id)
            struct.pack_into("<I", page.data, 12, new_id)
            page.dirty = True
            self.pages.init_heap(new_id, entry["id"], page_id)
            page_id, slots, free = new_id, 0, 16
            entry["tail"] = new_id
            entry["pages"] += 1
        page = self.pages.get_page(page_id)
        page.data[free:free + len(data)] = data
        struct.pack_into("<HH", page.data, PAGE_SIZE - 4 * (slots + 1), free, len(data))
        struct.pack_into("<HH", page.data, 4, slots + 1, free + len(data))
        page.dirty = True
        return page_id, slots

    def _delete(self, entry, rid):
        self._slot(entry, rid)
        page = self.pages.get_page(rid[0])
        struct.pack_into("<H", page.data, PAGE_SIZE - 4 * (rid[1] + 1), 0xFFFF)
        page.dirty = True

    def _indexes(self, table):
        return [i for i in self.meta["indexes"].values() if i["table"] == table.lower()]

    def _index_change(self, table, old, old_rid, new, new_rid):
        from storage.btree import BPlusTree
        for spec in self._indexes(table):
            tree = BPlusTree(self.pages, spec)
            column = spec["column"]
            if old is not None:
                tree.delete(old[column], old_rid)
            if new is not None:
                tree.insert(new[column], new_rid)

    def insert_record(self, table, row, columns):
        entry = self._entry(table)
        data = serialize(row, columns)
        rid = self._insert(entry, data)
        self._index_change(table, None, None, row, rid)
        entry["rows"] += 1
        entry["version"] += 1
        return rid

    def delete_record(self, table, rid):
        entry = self._entry(table)
        if self._slot(entry, rid, True) is None:
            return
        old = self.get_record(table, rid, self.catalog.find_table(table)["columns"])
        self._index_change(table, old, rid, None, None)
        self._delete(entry, rid)
        entry["rows"] -= 1
        entry["version"] += 1

    def update_record(self, table, rid, row, columns):
        data = serialize(row, columns)
        entry = self._entry(table)
        old = self.get_record(table, rid, columns)
        offset, length = self._slot(entry, rid)
        new_rid = rid
        if len(data) <= length:
            page = self.pages.get_page(rid[0])
            page.data[offset:offset + len(data)] = data
            struct.pack_into("<H", page.data, PAGE_SIZE - 4 * (rid[1] + 1) + 2, len(data))
            page.dirty = True
        else:
            new_rid = self._insert(entry, data)
            self._delete(entry, rid)
        self._index_change(table, old, rid, row, new_rid)
        entry["version"] += 1
        return new_rid

    def flush(self):
        self.pages.flush_all()

    def close(self):
        self.pages.close()


class V2Catalog(Catalog):
    """目录的内存视图随事务刷新，写入由会话提交统一持久化。"""

    def __init__(self, storage):
        super().__init__()
        self.storage = storage
        storage.catalog = self
        self.reload()

    def reload(self):
        self._tables = {name: {"name": entry["name"], "columns": [
            ColumnDef(c["name"], c["type"], nullable=c["nullable"], line=c["line"], column=c["column"])
            for c in entry["columns"]]} for name, entry in self.storage.meta["tables"].items()}

    def create_table(self, name, columns):
        self._validate(name, columns)
        self.storage.create_table(name, columns)
        self.reload()
