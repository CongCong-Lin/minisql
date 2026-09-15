"""本机数据库的跨进程读写锁；锁文件在释放后仍然保留。"""

import threading
import time
import sys
from pathlib import Path

import portalocker

from sql_compiler.errors import ExecuteError


class DatabaseLock:
    """每个会话独占自身句柄；操作系统同时协调进程与独立会话。"""

    def __init__(self, directory: Path, timeout: float = 5):
        if sys.platform == "win32":
            try:
                import win32file  # Windows 共享文件锁所需的扩展。
            except ImportError as exc:
                raise ExecuteError("Windows 共享锁需要安装 portalocker[win32]") from exc
        if not 0 <= timeout < float("inf"):
            raise ValueError("锁等待时间必须是有限非负数")
        self.path = directory.resolve() / "minisql.lock"
        self.timeout = timeout
        self.handle = None
        self.guard = threading.RLock()

    def acquire(self, readonly=False):
        if self.handle is not None:
            raise ExecuteError("当前会话已经持锁，不能升级锁")
        handle = self.path.open("a+b")
        flags = portalocker.LOCK_SH if readonly else portalocker.LOCK_EX
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                try:
                    portalocker.lock(handle, flags | portalocker.LOCK_NB)
                    self.handle = handle
                    return
                except portalocker.exceptions.AlreadyLocked:
                    if time.monotonic() >= deadline:
                        raise ExecuteError("[LOCK_TIMEOUT] 等待数据库锁超时") from None
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        except Exception:
            handle.close()
            raise

    def release(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            try:
                portalocker.unlock(handle)
            finally:
                handle.close()
