"""C 负责：物理页读写、缓存、页分配及空闲链。"""

import os
import struct

from storage.buffer import BufferPool
from storage.page import PAGE_SIZE, Page
from sql_compiler.errors import ExecuteError

_NULL_PAGE = 0xFFFFFFFF
_HEADER = struct.Struct("<4sHHII")
_POINTER = struct.Struct("<I")


class PageStore:
    """构造时打开文件；导入模块本身不创建任何文件。"""

    def __init__(self, path: str, capacity: int = 64, policy: str = "LRU") -> None:
        self.path = path
        self.buffer = BufferPool(capacity=capacity, policy=policy)
        self._file = None
        self._closed = False
        self._page_count = 0
        try:
            try:
                self._file = open(path, "r+b", buffering=0)
            except FileNotFoundError:
                self._file = open(path, "x+b", buffering=0)
            size = os.fstat(self._file.fileno()).st_size
            if size == 0:
                data = bytearray(PAGE_SIZE)
                _HEADER.pack_into(data, 0, b"MSQL", 1, 0, _NULL_PAGE, 0)
                self._write_disk(0, data, append=True)
                self._page_count = 1
            else:
                if size % PAGE_SIZE:
                    raise ExecuteError("数据库文件长度未按页对齐")
                self._page_count = size // PAGE_SIZE
                self._walk_free_chain(cached=False)
        except (OSError, ValueError, ExecuteError) as exc:
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    pass
            self._closed = True
            if isinstance(exc, ExecuteError):
                raise
            raise ExecuteError(f"数据库文件打开失败：{exc}") from exc

    def _ensure_open(self) -> None:
        """关闭后的页操作不能继续访问句柄。"""
        if self._closed or self._file is None:
            raise ExecuteError("页存储已经关闭")

    def _check_page(self, page_id: int) -> None:
        """布尔值不能充当物理页号。"""
        self._ensure_open()
        if type(page_id) is not int or not 0 <= page_id < self._page_count:
            raise ExecuteError(f"无效页号：{page_id}")

    @staticmethod
    def _check_data(data: bytes) -> None:
        """外部错误缩放 bytearray 时，拒绝破坏物理页边界。"""
        if not isinstance(data, (bytes, bytearray)) or len(data) != PAGE_SIZE:
            raise ExecuteError("整页数据必须恰好为 4096 字节")

    def _read_disk(self, page_id: int) -> bytes:
        """原始读通道不影响缓存、计数或替换顺序。"""
        self._check_page(page_id)
        try:
            self._file.seek(page_id * PAGE_SIZE)
            data = self._file.read(PAGE_SIZE)
        except (OSError, ValueError) as exc:
            raise ExecuteError(f"读取页 {page_id} 失败：{exc}") from exc
        if len(data) != PAGE_SIZE:
            raise ExecuteError(f"读取页 {page_id} 不完整")
        return data

    def _write_disk(self, page_id: int, data: bytes, *, append: bool = False) -> None:
        """原始写通道不使缓存失效，供刷盘和淘汰使用。"""
        self._ensure_open()
        if append:
            if page_id != self._page_count or page_id >= _NULL_PAGE:
                raise ExecuteError("追加页号非法")
        else:
            self._check_page(page_id)
        self._check_data(data)
        try:
            self._file.seek(page_id * PAGE_SIZE)
            written = self._file.write(data)
        except (OSError, ValueError) as exc:
            raise ExecuteError(f"写入页 {page_id} 失败：{exc}") from exc
        if written != PAGE_SIZE:
            raise ExecuteError(f"写入页 {page_id} 不完整")

    def _writeback(self, page_id: int, page: Page) -> None:
        """成功写回才清脏；不得调用会移除缓存的公开 write_page。"""
        if page.id != page_id:
            raise ExecuteError("缓存页号被错误修改")
        self._check_data(page.data)
        if page.dirty:
            self._write_disk(page_id, page.data)
            page.dirty = False

    def read_page(self, page_id: int) -> bytes:
        """返回磁盘快照，不保证包含缓存中尚未刷写的修改。"""
        return self._read_disk(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        """成功整页覆盖后丢弃旧缓存，其旧脏内容永远不能再写回。"""
        self._write_disk(page_id, data)
        self.buffer._discard(page_id)

    def get_page(self, page_id: int) -> Page:
        """返回唯一缓存对象；失效或淘汰后的旧引用不得再修改。"""
        self._check_page(page_id)
        page = self.buffer._lookup(page_id)
        if page is None:
            # 先读完整目标页，失败时不必牺牲任何旧页。
            page = Page(page_id, bytearray(self._read_disk(page_id)))
            self.buffer._insert(page, self._writeback)
        return page

    def flush_page(self, page_id: int) -> None:
        """有效但未缓存的页无需操作，刷盘不改变替换次序。"""
        self._check_page(page_id)
        page = self.buffer._pages.get(page_id)
        if page is not None:
            self._writeback(page_id, page)

    def _walk_free_chain(self, *, cached: bool = True) -> list[int]:
        """从最新页数据验证空闲链；构造验证绕过缓存统计。"""
        read = (lambda pid: bytes(self.get_page(pid).data)) if cached else self._read_disk
        data = read(0)
        self._check_data(data)
        magic, version, count, head, reserved = _HEADER.unpack_from(data)
        if magic != b"MSQL" or version != 1 or reserved != 0 or count > 60:
            raise ExecuteError("数据库元数据页格式损坏")
        chain = []
        seen = set()
        while head != _NULL_PAGE:
            if head == 0 or head in seen:
                raise ExecuteError("空闲链包含页 0 或循环")
            self._check_page(head)
            seen.add(head)
            chain.append(head)
            free_data = read(head)
            self._check_data(free_data)
            if any(free_data[4:]):
                raise ExecuteError("空闲页保留区域损坏")
            head = _POINTER.unpack_from(free_data)[0]
        return chain

    def alloc_page(self) -> int:
        """优先复用链首，返回前清零；表页头由 B 初始化。"""
        self._ensure_open()
        chain = self._walk_free_chain()
        if chain:
            page_id = chain[0]
            new_head = chain[1] if len(chain) > 1 else _NULL_PAGE
            # 空闲链遍历可能已经淘汰页 0，必须重新获取最新页。
            meta = self.get_page(0)
            _POINTER.pack_into(meta.data, 8, new_head)
            meta.dirty = True
            # 先摘链再清零，失败可泄漏页，但不能留下指向零页的空闲链。
            self.flush_page(0)
            self.write_page(page_id, bytes(PAGE_SIZE))
            return page_id
        page_id = self._page_count
        self._write_disk(page_id, bytes(PAGE_SIZE), append=True)
        self._page_count += 1
        return page_id

    def free_page(self, page_id: int) -> None:
        """B 先解除表链引用；C 只验证物理页及空闲链状态。"""
        self._check_page(page_id)
        if page_id == 0:
            raise ExecuteError("不能释放页 0")
        chain = self._walk_free_chain()
        if page_id in chain:
            raise ExecuteError(f"页面已经释放：{page_id}")
        free_data = bytearray(PAGE_SIZE)
        _POINTER.pack_into(free_data, 0, chain[0] if chain else _NULL_PAGE)
        self.write_page(page_id, free_data)
        meta = self.get_page(0)
        _POINTER.pack_into(meta.data, 8, page_id)
        meta.dirty = True

    def stats(self) -> dict:
        """关闭后仍可读取最终统计，不打印或重置。"""
        return self.buffer._stats()

    def flush_all(self) -> None:
        """只写回脏页；失败页保持脏状态并向上传播。"""
        self._ensure_open()
        for page_id, page in self.buffer._pages.items():
            self._writeback(page_id, page)

    def close(self) -> None:
        """尝试刷盘并释放句柄，保留最早故障；正常关闭可重复调用。"""
        if self._closed:
            return
        failure = None
        try:
            self.flush_all()
        except ExecuteError as exc:
            failure = exc
        finally:
            try:
                self._file.close()
            except (OSError, ValueError) as exc:
                if failure is None:
                    failure = ExecuteError(f"关闭数据库失败：{exc}")
            finally:
                self._closed = True
        if failure is not None:
            raise failure
