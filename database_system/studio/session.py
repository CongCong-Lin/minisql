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


def format_cell(value) -> str:
    """结果格展示：空值显示为 NULL，布尔与 SQL 字面量一致。"""
    if value is None:
        return "NULL"
    if value is True:
        return "TRUE"
    if value is False:
        return "FALSE"
    return str(value)


def token_span(tokens: list | None) -> dict | None:
    """把语句 Token 范围转成 Tk Text 下标（行从 1 计，列从 0 计）。"""
    if not tokens:
        return None
    body = [token for token in tokens if token.get("type") != "EOF"]
    if not body:
        return None
    start, end = body[0], body[-1]
    try:
        start_line = int(start.get("line") or 1)
        start_col = max(0, int(start.get("column") or 1) - 1)
        end_line = int(end.get("line") or start_line)
        lexeme = str(end.get("lexeme") or "")
        end_col = max(0, int(end.get("column") or 1) - 1) + len(lexeme)
    except (TypeError, ValueError):
        return None
    return {"start": f"{start_line}.{start_col}", "end": f"{end_line}.{end_col}"}


def format_plan_tree(node, indent: int = 0) -> str:
    """按 child / left / right 展开计划树，覆盖连接、排序、聚合与更新。"""
    pad = "  " * indent
    if isinstance(node, dict) and ("estimated_cost" in node or node.get("op") == "IndexScan"):
        from engine.physical import format_plan
        return format_plan(node, indent)
    if node is None:
        return f"{pad}(空)"
    if not isinstance(node, dict):
        return f"{pad}{node}"
    op = node.get("op") or node.get("node") or "node"
    detail = _plan_detail(node)
    line = f"{pad}{op}" + (f"  {detail}" if detail else "")
    parts = [line]
    for key in ("child", "left", "right"):
        child = node.get(key)
        if isinstance(child, dict):
            if key != "child":
                parts.append(f"{pad}  [{key}]")
            parts.append(format_plan_tree(child, indent + 1))
    return "\n".join(parts)


def _plan_detail(node: dict) -> str:
    op = node.get("op")
    if op == "SeqScan":
        return str(node.get("table") or "")
    if op == "Project":
        items = node.get("items") or []
        if items:
            return ", ".join(str(item.get("label") or "") for item in items if isinstance(item, dict))
        columns = node.get("columns") or []
        names = []
        for column in columns:
            if isinstance(column, dict):
                names.append(str(column.get("name") or column.get("label") or ""))
            else:
                names.append(str(column))
        return ", ".join(name for name in names if name)
    if op == "Sort":
        bits = []
        for key in node.get("keys") or []:
            if not isinstance(key, dict):
                continue
            expr = key.get("expr") if isinstance(key.get("expr"), dict) else {}
            name = expr.get("name") or expr.get("op") or ""
            bits.append(f"{name} {'DESC' if key.get('descending') else 'ASC'}".strip())
        return ", ".join(bits)
    if op == "NestedLoopJoin":
        return "INNER"
    if op == "Aggregate":
        names = [
            str(item.get("function") or "")
            for item in (node.get("aggregates") or [])
            if isinstance(item, dict)
        ]
        return ", ".join(name for name in names if name)
    if op == "Update":
        return str(node.get("table") or "")
    return str(node.get("table") or "")


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
        "error_code": result.error_code,
        "semantic_ok": result.semantic_ok,
        "tokens": result.tokens,
        "span": token_span(result.tokens),
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
        return self.catalog is not None and not getattr(getattr(self.catalog, "_session", None), "closed", False)

    @property
    def backend(self):
        return getattr(self.catalog, "_session", None)

    def tables(self) -> list[dict]:
        if self.catalog is None:
            return []
        items = []
        if self.backend is not None and self.backend.state in {"failed", "broken"}:
            return []
        catalog = self.backend.catalog_snapshot() if self.backend is not None else self.catalog
        for name in catalog.list_tables():
            schema = catalog.find_table(name) or {"name": name, "columns": []}
            items.append({
                "name": schema["name"],
                "columns": [
                    {"name": column.name, "type": column.col_type}
                    for column in schema["columns"]
                ],
            })
        return items

    def open(self, data_dir: str, mode: str = "database", *, username=None, password=None, timeout=5) -> list[dict]:
        if mode not in ("compiler", "database"):
            raise ExecuteError(f"unsupported mode: {mode}")
        self.close()
        root = Path(data_dir).expanduser().resolve()
        catalog, storage = open_database(str(root), mode=mode, username=username, password=password, timeout=timeout)
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
            "transaction_state": self.backend.state if self.backend else "idle",
            "indexes": self.indexes(),
        }

    def indexes(self):
        if self.backend is None or self.backend.closed or self.backend.state == "failed":
            return []
        with self.backend.transaction(True):
            from engine.security import visible_tables
            visible = visible_tables(self.backend)
            return [{key: value for key, value in spec.items() if key in {"name", "table", "column", "type", "height", "entries"}}
                    for spec in self.storage.meta["indexes"].values() if spec["table"] in visible]

    def admin(self, action, name, password=None):
        if self.backend is None:
            raise ExecuteError("账号管理需要数据库模式连接")
        from engine.security import initialize, manage_user
        if action == "init":
            initialize(self.backend, name, password)
        else:
            manage_user(self.backend, action, name, password)

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
        if self.backend is not None:
            if self.backend.closed or self.backend.state == "failed":
                return []
            return self.backend.inspect(sql)
        return inspect_sql(sql, self.catalog)
