"""单写事务的页级撤销日志；提交点为提交标记成功同步。"""

import os
import struct
import uuid
import zlib
from pathlib import Path

from sql_compiler.errors import ExecuteError

PAGE_SIZE = 4096
HEADER = struct.Struct("<8s16sQ")
MAGIC = b"MSQLUNDO"


class UndoJournal:
    """调用者必须持数据库独占锁；恢复过程可以重复执行。"""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None
        self.seen = set()
        self.original_size = 0
        self.transaction = b""
        self.commit_started = False
        self.committed = False
        self.hook = None

    def _event(self, name):
        if self.hook is not None:
            self.hook(name)

    @staticmethod
    def _sync(stream):
        stream.flush()
        os.fsync(stream.fileno())

    def begin(self, data):
        self.original_size = os.fstat(data.fileno()).st_size
        self.transaction = uuid.uuid4().bytes
        self.seen = set()
        self.commit_started = self.committed = False
        self.stream = self.path.open("w+b", buffering=0)
        self._sync(self.stream)
        body = HEADER.pack(MAGIC, self.transaction, self.original_size)
        self._write(body + struct.pack("<I", zlib.crc32(body)))
        self._sync(self.stream)
        self._event("日志头已同步")

    def _write(self, data):
        if self.stream.write(data) != len(data):
            raise OSError("撤销日志写入不完整")

    def before_write(self, data, page_id):
        if self.stream is None or page_id in self.seen:
            return
        offset = page_id * PAGE_SIZE
        if offset < self.original_size:
            data.seek(offset)
            old = data.read(PAGE_SIZE)
            if len(old) != PAGE_SIZE:
                raise ExecuteError("撤销日志无法读取完整旧页")
            body = b"P" + self.transaction + struct.pack("<I", page_id) + old
            self._write(body + struct.pack("<I", zlib.crc32(body)))
            self._sync(self.stream)
            self._event("旧页已同步")
        self.seen.add(page_id)

    def commit(self, data):
        self._sync(data)
        self._event("数据已同步")
        body = b"C" + self.transaction
        self.commit_started = True
        self._write(body + struct.pack("<I", zlib.crc32(body)))
        self._sync(self.stream)
        self.committed = True
        self._event("提交已同步")
        self.detach()
        try:
            self.clear()
        except OSError:
            # 已越过提交点，残留日志由下次打开清理，不能撤销已提交数据。
            return "提交成功，日志待清理"
        return None

    def detach(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            stream.close()

    def clear(self):
        with self.path.open("w+b", buffering=0) as stream:
            self._sync(stream)

    def pending(self):
        return self.path.exists() and self.path.stat().st_size > 0

    def recover(self, data):
        self.detach()
        if not self.pending():
            return
        raw = self.path.read_bytes()
        start = HEADER.size + 4
        if len(raw) < start:
            self.clear()
            return
        body = raw[:HEADER.size]
        if zlib.crc32(body) != struct.unpack_from("<I", raw, HEADER.size)[0]:
            raise ExecuteError("撤销日志头校验失败")
        magic, transaction, original_size = HEADER.unpack(body)
        if magic != MAGIC or original_size % PAGE_SIZE:
            raise ExecuteError("撤销日志格式非法")
        pages, committed, seen = [], False, set()
        pos = start
        while pos < len(raw):
            tag = raw[pos:pos + 1]
            length = 1 + 16 + (4 + PAGE_SIZE if tag == b"P" else 0) + 4
            if tag not in {b"P", b"C"}:
                raise ExecuteError("撤销日志记录类型非法")
            if pos + length > len(raw):
                break  # 未同步完整的末尾记录对应的数据写入尚未获准。
            record = raw[pos:pos + length - 4]
            checksum = struct.unpack_from("<I", raw, pos + length - 4)[0]
            if record[1:17] != transaction or zlib.crc32(record) != checksum:
                raise ExecuteError("撤销日志记录校验失败")
            if tag == b"C":
                if pos + length != len(raw):
                    raise ExecuteError("提交标记之后出现多余日志")
                committed = True
                break
            page_id = struct.unpack_from("<I", record, 17)[0]
            if page_id * PAGE_SIZE >= original_size or page_id in seen:
                raise ExecuteError("撤销日志页号非法")
            seen.add(page_id)
            pages.append((page_id, record[21:]))
            pos += length
        if not committed:
            for page_id, old in pages:
                data.seek(page_id * PAGE_SIZE)
                if data.write(old) != PAGE_SIZE:
                    raise OSError("恢复页写入不完整")
                self._event("恢复页已写入")
            data.truncate(original_size)
            self._sync(data)
        self.clear()
