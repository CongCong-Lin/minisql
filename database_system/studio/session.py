"""桌面客户端与 runtime 之间的会话，不依赖界面库。"""

from __future__ import annotations

import json
from pathlib import Path

from engine.runtime import close_database, open_database, run
from sql_compiler.errors import ExecuteError
from studio.inspect import Diagnostic, inspect_sql
from utils.results import StmtResult


def json_default(value):
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(type(value).__name__)


def pretty(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=json_default)


def serialize_result(result: StmtResult) -> dict:
    exec_result = result.exec_result
    execution = None
    if exec_result is not None:
        execution = {
            "columns": list(exec_result.columns),
            "rows": [list(row) for row in exec_result.rows],
            "message": exec_result.message,
            "is_query": bool(exec_result.columns) or "selected" in exec_result.message,
        }
    plans = result.plans or []
    return {
        "ok": result.ok,
        "error": result.error,
        "semantic_ok": result.semantic_ok,
        "tokens": result.tokens,
        "ast": result.ast,
        "plan": plans[0] if plans else None,
        "opt_plan": plans[1] if len(plans) > 1 else None,
        "exec": execution,
    }


class StudioSession:
    """对应 CLI 的 open → run → close，供窗口复用。"""

    def __init__(self) -> None:
        self.catalog = None
        self.storage = None
        self.mode: str | None = None
        self.data_dir: str | None = None

    @property
    def connected(self) -> bool:
        return self.catalog is not None

    def tables(self) -> list[dict]:
        if self.catalog is None:
            return []
        items = []
        for name in self.catalog.list_tables():
            schema = self.catalog.find_table(name) or {"name": name, "columns": []}
            items.append({
                "name": schema["name"],
                "columns": [
                    {"name": column.name, "type": column.col_type}
                    for column in schema["columns"]
                ],
            })
        return items

    def open(self, data_dir: str, mode: str = "database") -> list[dict]:
        if mode not in ("compiler", "database"):
            raise ExecuteError(f"unsupported mode: {mode}")
        self.close()
        root = Path(data_dir).expanduser().resolve()
        catalog, storage = open_database(str(root), mode=mode)
        self.catalog = catalog
        self.storage = storage
        self.mode = mode
        self.data_dir = str(root)
        return self.tables()

    def run_sql(self, sql: str) -> dict:
        if self.catalog is None:
            raise ExecuteError("尚未打开数据库，请先连接")
        if not isinstance(sql, str):
            raise ExecuteError("sql 必须是字符串")
        results = run(sql, self.catalog, self.storage)
        return {
            "ok": True,
            "mode": self.mode,
            "results": [serialize_result(item) for item in results],
            "tables": self.tables(),
            "sql_failed": any(not item.ok for item in results),
        }

    def close(self) -> None:
        catalog, storage = self.catalog, self.storage
        self.catalog = None
        self.storage = None
        self.mode = None
        self.data_dir = None
        if catalog is not None:
            close_database(catalog, storage)

    def inspect(self, sql: str) -> list[Diagnostic]:
        """实时检查当前窗口 SQL，不执行、不改目录。"""
        return inspect_sql(sql, self.catalog)
