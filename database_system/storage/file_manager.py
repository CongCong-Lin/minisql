"""C 负责：物理页、空闲链及缓存对外接口。"""

from storage.buffer import BufferPool
from storage.page import Page
from sql_compiler.errors import ExecuteError


class PageStore:
    """构造仅保存配置，不打开或创建磁盘文件。"""

    def __init__(self, path: str, capacity: int = 64, policy: str = "LRU") -> None:
        """保存磁盘路径和缓存依赖，I/O 行为待实现。"""
        self.path = path
        self.buffer = BufferPool(capacity=capacity, policy=policy)

    def read_page(self, page_id: int) -> bytes:
        """绕过缓存读取磁盘页快照。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def write_page(self, page_id: int, data: bytes) -> None:
        """直接整页写盘并使旧缓存失效。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def get_page(self, page_id: int) -> Page:
        """取得缓存页并维护命中与替换统计。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def flush_page(self, page_id: int) -> None:
        """写回指定脏页。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def alloc_page(self) -> int:
        """从空闲链或文件尾分配并清零新页。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def free_page(self, page_id: int) -> None:
        """回收已经脱离所有表链的非零页。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def stats(self) -> dict:
        """返回命中、未命中和淘汰次数，不打印。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def flush_all(self) -> None:
        """写回所有脏页。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")

    def close(self) -> None:
        """刷盘并关闭文件，完成实现后须支持重复调用。"""
        raise NotImplementedError("M1 存根：页存储由 C 实现")
