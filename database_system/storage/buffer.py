"""C 负责：缓存替换实现位置，公开页入口位于 PageStore。"""


class BufferPool:
    """M1 只保存容量和策略，缓存算法由 C 后续实现。"""

    def __init__(self, capacity: int = 64, policy: str = "LRU") -> None:
        """记录有效配置，支持容量为 1 的边界测试。"""
        if capacity < 1:
            raise ValueError("缓存容量必须至少为 1")
        if policy not in {"LRU", "FIFO"}:
            raise ValueError("缓存策略必须为 LRU 或 FIFO")
        self.capacity = capacity
        self.policy = policy
