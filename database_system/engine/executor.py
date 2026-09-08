"""D 负责：消费计划并通过表级接口执行。"""

from sql_compiler.catalog import Catalog
from sql_compiler.errors import ExecuteError
from storage.storage_engine import StorageEngine
from utils.results import ExecuteResult


def execute(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult:
    """执行单条计划，成功刷盘后返回结果。"""
    raise NotImplementedError("M1 存根：执行引擎由 D 实现")
