"""D 负责：消费计划并通过表级接口执行。"""

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ExecuteError
from engine.evaluator import evaluate
from storage.storage_engine import RecordId, StorageEngine
from utils.results import ExecuteResult


def execute(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult:
    """执行单条计划，成功刷盘后返回结果。"""
    op = plan.get("op")
    if op == "CreateTable":
        result = _execute_create(plan, catalog)
    elif op == "Insert":
        result = _execute_insert(plan, catalog, storage)
    elif op == "Project":
        if "items" in plan:
            from engine.relational import execute_query
            result = execute_query(plan, catalog, storage)
        else:
            result = _execute_project(plan, catalog, storage)
    elif op == "Delete":
        result = _execute_delete(plan, catalog, storage)
    elif op == "Update":
        result = _execute_update(plan, catalog, storage)
    else:
        raise ExecuteError(f"unsupported plan operator '{op}'")
    storage.flush()
    return result


def _execute_create(plan: dict, catalog: Catalog) -> ExecuteResult:
    """从计划重建列定义，只调用一次 Catalog.create_table。"""
    columns = [
        ColumnDef(
            item["name"], item["type"],
            nullable=item.get("nullable", True),
            line=item["line"], column=item["column"],
        )
        for item in plan["columns"]
    ]
    catalog.create_table(plan["table"], columns)
    return ExecuteResult([], "OK", [])


def _execute_insert(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult:
    """按 Catalog 列序重排 INSERT 值后追加一行。"""
    schema = _require_table(catalog, plan["table"])
    values_by_name = {
        name.lower(): value
        for name, value in zip(plan["columns"], plan["values"])
    }
    row = tuple(values_by_name[column.name.lower()] for column in schema["columns"])
    storage.insert_record(plan["table"], row, schema["columns"])
    return ExecuteResult([], "1 row inserted", [])


def _execute_project(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult:
    """投影查询列并丢弃内部记录标识。"""
    table = _plan_table(plan)
    schema = _require_table(catalog, table)
    selected = _project_headers(plan["columns"], schema["columns"])
    rows = []
    for _rid, row, columns in _scan(plan["child"], catalog, storage):
        rows.append(_project_row(row, columns, plan["columns"]))
    count = len(rows)
    message = f"{count} row selected" if count == 1 else f"{count} rows selected"
    return ExecuteResult(rows, message, selected)


def _execute_delete(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult:
    """先收集全部目标记录标识，再标记删除。"""
    targets = [
        (rid, row, columns)
        for rid, row, columns in _scan(plan["child"], catalog, storage)
    ]
    for rid, _row, _columns in targets:
        storage.delete_record(plan["table"], rid)
    count = len(targets)
    message = f"{count} row deleted" if count == 1 else f"{count} rows deleted"
    return ExecuteResult([], message, [])


def _execute_update(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult:
    """计算及编码预检全部新行后写入，赋值之间不会观察到中间结果。"""
    from storage.record import serialize
    targets = []
    for rid, old_row, columns in _scan(plan["child"], catalog, storage):
        new_row = list(old_row)
        for assignment in plan["assignments"]:
            new_row[assignment["index"]] = evaluate(assignment["expr"], old_row, columns)
        new_row = tuple(new_row)
        serialize(new_row, columns)
        targets.append((rid, new_row, columns))
    for rid, row, columns in targets:
        storage.update_record(plan["table"], rid, row, columns)
    count = len(targets)
    message = f"{count} row updated" if count == 1 else f"{count} rows updated"
    return ExecuteResult([], message, [])


def _scan(plan: dict, catalog: Catalog, storage: StorageEngine
          ) -> list[tuple[RecordId, tuple, list[ColumnDef]]]:
    """扫描或过滤，内部保留记录标识和完整行。"""
    op = plan.get("op")
    if op in {"SeqScan", "IndexScan"}:
        schema = _require_table(catalog, plan["table"])
        if op == "IndexScan":
            from engine.physical import scan_index
            return [(rid, row, schema["columns"]) for rid, row in scan_index(plan, storage, schema["columns"])]
        return [
            (rid, row, schema["columns"])
            for rid, row in storage.scan_records(plan["table"], schema["columns"])
        ]
    if op == "Filter":
        kept = []
        for rid, row, columns in _scan(plan["child"], catalog, storage):
            if evaluate(plan["predicate"], row, columns) is True:
                kept.append((rid, row, columns))
        return kept
    raise ExecuteError(f"unsupported scan operator '{op}'")


def _require_table(catalog: Catalog, table: str):
    """查找用户表，缺失时转为执行错误。"""
    schema = catalog.find_table(table)
    if schema is None:
        raise ExecuteError(f"table '{table}' does not exist")
    return schema


def _plan_table(plan: dict) -> str:
    """沿子计划找到扫描表名。"""
    node: dict | None = plan
    while node:
        if "table" in node:
            return node["table"]
        node = node.get("child")
    raise ExecuteError("plan is missing table")


def _project_headers(select_columns: list[str] | str, schema_columns: list[ColumnDef]) -> list[str]:
    """查询表头使用 Catalog 保存的列名。"""
    if select_columns == "*":
        return [column.name for column in schema_columns]
    lookup = {column.name.lower(): column.name for column in schema_columns}
    return [lookup[name.lower()] for name in select_columns]


def _project_row(row: tuple, schema_columns: list[ColumnDef],
                 select_columns: list[str] | str) -> tuple:
    """按 SELECT 列表投影一行。"""
    if select_columns == "*":
        return tuple(row)
    lookup = {column.name.lower(): index for index, column in enumerate(schema_columns)}
    return tuple(row[lookup[name.lower()]] for name in select_columns)
