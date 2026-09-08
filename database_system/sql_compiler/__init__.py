"""编译器公开接口；延迟导入避免包初始化循环。"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sql_compiler.catalog import Catalog
    from utils.results import StmtResult


def compile_sql(text: str, catalog: Catalog) -> list[StmtResult]:
    """在独立目录快照中编译输入，失败时抛出公共编译异常。"""
    from engine.runtime import _compile_sql
    return _compile_sql(text, catalog)
