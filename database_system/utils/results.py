"""D 负责：CLI、编译器和执行器共享的结果结构。"""

from dataclasses import dataclass


@dataclass
class ExecuteResult:
    """查询返回行和列名，非查询两者均为空列表。"""

    rows: list[tuple]
    message: str
    columns: list[str]


@dataclass
class StmtResult:
    """保存单个分段的完成状态及已完成阶段的快照。"""

    ok: bool
    error: str | None
    tokens: list[dict] | None
    ast: list[dict] | None
    plans: list[dict]
    exec_result: ExecuteResult | None
    semantic_ok: bool | None
