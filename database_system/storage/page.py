"""C 负责：缓存中共享的页对象。"""

from dataclasses import dataclass, field

PAGE_SIZE = 4096


@dataclass
class Page:
    """保存页号、独立的固定大小缓冲和脏标志。"""

    id: int
    data: bytearray = field(default_factory=lambda: bytearray(PAGE_SIZE))
    dirty: bool = False
