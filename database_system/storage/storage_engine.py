"""B 负责：表映射、记录定位、页链和槽操作。"""

from collections.abc import Iterator
import struct
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from storage.file_manager import PageStore
from storage.page import PAGE_SIZE
from storage.record import serialize, deserialize

RecordId = tuple[int, int]


class StorageEngine:
    """构造仅注入页后端，不读取映射或创建系统表。"""

    def __init__(self, pages: PageStore) -> None:
        """保存页后端，以便使用测试替身独立开发。"""
        self.pages = pages

    def has_table(self, table: str) -> bool:
        """检查物理映射中是否存在指定表。"""
        return self._find_table(table) is not None

    def create_table(self, table: str) -> None:
        """检查容量、分配首页并登记物理映射，不操作 Catalog。"""
        try:
            name = table.lower().encode("ascii")
        except UnicodeEncodeError:
            raise ExecuteError("table name must be ASCII")
        if self.has_table(table): raise ExecuteError("table already exists")
        if len(name) > 64: raise ExecuteError("table name exceeds 64 bytes")
        meta = self.pages.get_page(0)
        magic, version, count, free, reserved = struct.unpack_from("<4sHHII", meta.data)
        if magic != b"MSQL" or version != 1: raise ExecuteError("invalid metadata page")
        if count >= 60: raise ExecuteError("table limit exceeded (59 user tables)")
        page_id = self.pages.alloc_page()
        page = self.pages.get_page(page_id)
        page.data[:] = b"\x00" * PAGE_SIZE
        struct.pack_into("<IHHII", page.data, 0, page_id, 0, 16, 0xFFFFFFFF, 0xFFFFFFFF)
        page.dirty = True
        meta = self.pages.get_page(0)
        _, _, count, _, _ = struct.unpack_from("<4sHHII", meta.data)
        offset = 16 + count * 68
        meta.data[offset:offset + 64] = name.ljust(64, b"\x00")
        struct.pack_into("<I", meta.data, offset + 64, page_id)
        struct.pack_into("<H", meta.data, 6, count + 1)
        meta.dirty = True

    def insert_record(self, table: str, row: tuple,
                      columns: list[ColumnDef]) -> RecordId:
        """预检记录容量并向表尾追加，返回页号和槽号。"""
        entry = self._find_table(table)
        if entry is None: raise ExecuteError("table does not exist")
        encoded = serialize(row, columns)
        page_id = entry[1]
        visited = set()
        while True:
            if page_id in visited: raise ExecuteError("corrupt page chain")
            visited.add(page_id)
            page = self.pages.get_page(page_id)
            header = self._header(page, page_id)
            pid, slots, free, prev, nxt = header
            if page_id == entry[1] and prev != 0xFFFFFFFF:
                raise ExecuteError("corrupt page chain")
            if page_id != entry[1] and prev != previous_page_id:
                raise ExecuteError("corrupt page chain")
            if free + len(encoded) <= PAGE_SIZE - 4 * (slots + 1):
                page.data[free:free + len(encoded)] = encoded
                struct.pack_into("<HH", page.data, PAGE_SIZE - 4 * (slots + 1), free, len(encoded))
                struct.pack_into("<H", page.data, 4, slots + 1)
                struct.pack_into("<H", page.data, 6, free + len(encoded))
                page.dirty = True
                return page_id, slots
            if nxt == 0xFFFFFFFF:
                new_id = self.pages.alloc_page()
                page = self.pages.get_page(page_id)
                struct.pack_into("<I", page.data, 12, new_id)
                page.dirty = True
                new = self.pages.get_page(new_id)
                new.data[:] = b"\x00" * PAGE_SIZE
                struct.pack_into("<IHHII", new.data, 0, new_id, 0, 16, page_id, 0xFFFFFFFF)
                new.dirty = True
                previous_page_id = page_id
                page_id = new_id
            else:
                previous_page_id = page_id
                page_id = nxt

    def scan_records(self, table: str, columns: list[ColumnDef]
                     ) -> Iterator[tuple[RecordId, tuple]]:
        """按页链和槽号扫描，跳过墓碑并保留记录位置。"""
        entry = self._find_table(table)
        if entry is None: raise ExecuteError("table does not exist")
        page_id, previous_page_id, seen = entry[1], 0xFFFFFFFF, set()
        while page_id != 0xFFFFFFFF:
            if page_id in seen: raise ExecuteError("corrupt page chain")
            seen.add(page_id); page = self.pages.get_page(page_id)
            _, slots, free, prev, nxt = self._header(page, page_id)
            if prev != previous_page_id: raise ExecuteError("corrupt page chain")
            rows = []
            for slot in range(slots):
                offset, length = struct.unpack_from("<HH", page.data, PAGE_SIZE - 4 * (slot + 1))
                if offset == 0xFFFF: continue
                directory = PAGE_SIZE - 4 * slots
                if (offset < 16 or length == 0 or offset + length > free
                        or offset + length > directory):
                    raise ExecuteError("corrupt slot")
                rows.append((slot, bytes(page.data[offset:offset + length])))
            for slot, data in rows: yield (page_id, slot), deserialize(data, columns)
            previous_page_id, page_id = page_id, nxt

    def delete_record(self, table: str, rid: RecordId) -> None:
        """按记录位置标记墓碑，不按行值反查或去重。"""
        entry = self._find_table(table)
        if entry is None: raise ExecuteError("table does not exist")
        if (not isinstance(rid, tuple) or len(rid) != 2
                or any(isinstance(value, bool) or not isinstance(value, int)
                       for value in rid)):
            raise ExecuteError("invalid record id")
        page_id, slot = rid
        if page_id <= 0 or slot < 0:
            raise ExecuteError("invalid record id")
        if not self._belongs(entry[1], page_id): raise ExecuteError("record id does not belong to table")
        page = self.pages.get_page(page_id)
        self._header(page, page_id)
        if slot < 0 or slot >= struct.unpack_from("<H", page.data, 4)[0]: raise ExecuteError("invalid record id")
        offset = PAGE_SIZE - 4 * (slot + 1)
        current, length = struct.unpack_from("<HH", page.data, offset)
        if current == 0xFFFF: return
        free = struct.unpack_from("<H", page.data, 6)[0]
        directory = PAGE_SIZE - 4 * struct.unpack_from("<H", page.data, 4)[0]
        if (current < 16 or length == 0 or current + length > free
                or current + length > directory):
            raise ExecuteError("corrupt slot")
        struct.pack_into("<H", page.data, offset, 0xFFFF); page.dirty = True

    def flush(self) -> None:
        """委托页后端写回全部脏页。"""
        self.pages.flush_all()

    def update_record(self, table: str, rid: RecordId, row: tuple,
                      columns: list[ColumnDef]) -> RecordId:
        """短记录原位替换，长记录先插入再删除旧槽，不改变页格式。"""
        encoded = serialize(row, columns)
        entry = self._find_table(table)
        if entry is None:
            raise ExecuteError("table does not exist")
        if (not isinstance(rid, tuple) or len(rid) != 2
                or any(type(value) is not int for value in rid)):
            raise ExecuteError("invalid record id")
        page_id, slot = rid
        if page_id <= 0 or slot < 0:
            raise ExecuteError("invalid record id")
        if not self._belongs(entry[1], page_id):
            raise ExecuteError("record id does not belong to table")
        page = self.pages.get_page(page_id)
        _, slots, free, _, _ = self._header(page, page_id)
        if slot >= slots:
            raise ExecuteError("invalid record id")
        directory = PAGE_SIZE - 4 * (slot + 1)
        offset, length = struct.unpack_from("<HH", page.data, directory)
        if offset == 0xFFFF:
            raise ExecuteError("cannot update a deleted record")
        if offset < 16 or length == 0 or offset + length > min(free, PAGE_SIZE - 4 * slots):
            raise ExecuteError("corrupt slot")
        if len(encoded) <= length:
            page.data[offset:offset + len(encoded)] = encoded
            struct.pack_into("<H", page.data, directory + 2, len(encoded))
            page.dirty = True
            return rid
        # 插入可能触发淘汰；不再使用之前持有的 page 引用。
        new_rid = self.insert_record(table, row, columns)
        self.delete_record(table, rid)
        return new_rid

    def close(self) -> None:
        """委托页后端刷盘关闭，完成实现后须支持重复调用。"""
        self.pages.close()

    def _find_table(self, table: str):
        meta = self.pages.get_page(0)
        magic, version, count, _, _ = struct.unpack_from("<4sHHII", meta.data)
        if magic != b"MSQL" or version != 1 or count > 60: raise ExecuteError("invalid metadata page")
        try:
            wanted = table.lower().encode("ascii")
        except UnicodeEncodeError:
            return None
        for i in range(count):
            offset = 16 + i * 68
            name = bytes(meta.data[offset:offset + 64]).split(b"\x00", 1)[0]
            page_id = struct.unpack_from("<I", meta.data, offset + 64)[0]
            if not name or page_id == 0 or page_id == 0xFFFFFFFF:
                raise ExecuteError("invalid table mapping")
            if name == wanted: return name, page_id
        return None

    def _header(self, page, expected):
        if len(page.data) != PAGE_SIZE: raise ExecuteError("invalid page size")
        header = struct.unpack_from("<IHHII", page.data)
        max_slots = (PAGE_SIZE - 16) // 4
        directory = PAGE_SIZE - 4 * header[1]
        if (header[0] != expected or expected == 0 or header[1] > max_slots
                or header[2] < 16 or header[2] > directory
                or header[3] == 0 or header[4] == 0):
            raise ExecuteError("corrupt data page")
        return header

    def _belongs(self, first, target):
        current, seen = first, set()
        while current != 0xFFFFFFFF:
            if current in seen: raise ExecuteError("corrupt page chain")
            seen.add(current)
            page = self.pages.get_page(current); header = self._header(page, current)
            if current == target: return True
            current = header[4]
        return False
