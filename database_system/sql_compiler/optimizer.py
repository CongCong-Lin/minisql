"""D 负责：保持结果及错误行为的计划优化。"""

from sql_compiler.errors import PlannerError


def optimize(plan: dict) -> tuple[dict, list[str]]:
    """返回独立优化计划和按首次触发顺序去重的规则名。"""
    raise NotImplementedError("M1 存根：计划优化由 D 实现")
