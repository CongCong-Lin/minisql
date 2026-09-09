"""C 负责：有界页缓存、替换顺序和请求统计。"""

from collections import OrderedDict
from collections.abc import Callable
import sys

from sql_compiler.errors import ExecuteError
from storage.page import Page


class BufferPool:
    """文件读写由 PageStore 提供，本类只维护缓存状态。"""

    def __init__(self, capacity: int = 64, policy: str = "LRU") -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("缓存容量必须至少为 1")
        if policy not in {"LRU", "FIFO"}:
            raise ValueError("缓存策略必须为 LRU 或 FIFO")
        self.capacity = capacity
        self.policy = policy
        self._pages: OrderedDict[int, Page] = OrderedDict()
        self._hits = self._misses = self._evictions = 0

    def _lookup(self, page_id: int) -> Page | None:
        """只对实际取页请求计数；LRU 命中更新次序。"""
        page = self._pages.get(page_id)
        if page is None:
            self._misses += 1
        else:
            self._hits += 1
            if self.policy == "LRU":
                self._pages.move_to_end(page_id)
        return page

    def _insert(self, page: Page, writeback: Callable[[int, Page], None]) -> None:
        """写回失败保留旧缓存；替换完成后才输出日志并上报日志故障。"""
        old_id = None
        if len(self._pages) >= self.capacity:
            old_id = next(iter(self._pages))
            old_page = self._pages[old_id]
            writeback(old_id, old_page)
            del self._pages[old_id]
            self._evictions += 1
        self._pages[page.id] = page
        if old_id is not None:
            try:
                print(f"[BUFFER] evict page={old_id}", file=sys.stderr)
            except (OSError, ValueError) as exc:
                raise ExecuteError(f"写入缓存淘汰日志失败：{exc}") from exc

    def _discard(self, page_id: int) -> None:
        """直接写盘导致的失效不计作容量淘汰。"""
        self._pages.pop(page_id, None)

    def _stats(self) -> dict:
        """返回独立字典，查询不改变计数。"""
        return {"hits": self._hits, "misses": self._misses,
                "evictions": self._evictions}
