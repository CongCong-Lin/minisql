"""扩展查询的行集合执行，输出列结构与行数据分别保存。"""

from dataclasses import dataclass
import math

from engine.evaluator import evaluate
from engine.query_numbers import checked_query_integer
from sql_compiler.errors import ExecuteError
from utils.results import ExecuteResult


@dataclass
class Relation:
    """即使没有记录，列描述仍然完整。"""

    columns: list[dict]
    rows: list[tuple]


def execute_query(plan, catalog, storage):
    """执行最终投影，内部列绑定不依赖显示表头是否重复。"""
    relation = evaluate_relation(plan, catalog, storage)
    count = len(relation.rows)
    message = f"{count} row selected" if count == 1 else f"{count} rows selected"
    return ExecuteResult(relation.rows, message, [column["name"] for column in relation.columns])


def evaluate_relation(plan, catalog, storage):
    """递归执行关系算子，内部列索引与输入行结构一致。"""
    op = plan["op"]
    if op in {"SeqScan", "IndexScan"}:
        schema = catalog.find_table(plan["table"])
        if schema is None:
            raise ExecuteError(f"table '{plan['table']}' does not exist")
        columns = plan.get("output") or [{"name": column.name, "type": column.col_type}
                                         for column in schema["columns"]]
        if op == "IndexScan":
            from engine.physical import scan_index
            rows = [row for _rid, row in scan_index(plan, storage, schema["columns"])]
        else:
            rows = [row for _rid, row in storage.scan_records(plan["table"], schema["columns"])]
        return Relation(columns, rows)
    if op == "Filter":
        source = evaluate_relation(plan["child"], catalog, storage)
        return Relation(source.columns, [row for row in source.rows
                                         if evaluate(plan["predicate"], row, []) is True])
    if op == "NestedLoopJoin":
        left = evaluate_relation(plan["left"], catalog, storage)
        right = evaluate_relation(plan["right"], catalog, storage)
        rows = []
        for left_row in left.rows:
            for right_row in right.rows:
                combined = left_row + right_row
                if evaluate(plan["predicate"], combined, []) is True:
                    rows.append(combined)
        return Relation(left.columns + right.columns, rows)
    if op == "Aggregate":
        source = evaluate_relation(plan["child"], catalog, storage)
        return aggregate_relation(source, plan["keys"], plan["aggregates"])
    if op == "Sort":
        source = evaluate_relation(plan["child"], catalog, storage)
        # 每个键在每行上仅求值一次，排序中的比较不重新执行表达式。
        decorated = [(row, [evaluate(key["expr"], row, []) for key in plan["keys"]])
                     for row in source.rows]
        for index in range(len(plan["keys"]) - 1, -1, -1):
            decorated.sort(key=lambda item: (item[1][index] is not None, item[1][index]),
                           reverse=plan["keys"][index]["descending"])
        return Relation(source.columns, [row for row, _keys in decorated])
    if op == "Project":
        source = evaluate_relation(plan["child"], catalog, storage)
        items = plan["items"]
        return Relation([{"name": item["label"], "type": item["expr"]["value_type"]}
                         for item in items],
                        [tuple(evaluate(item["expr"], row, []) for item in items)
                         for row in source.rows])
    raise ExecuteError(f"unsupported relation operator '{op}'")


def aggregate_relation(source, keys, aggregates):
    """按首次遇到的键分组；无分组键时空输入也产生一个全表聚合组。"""
    groups = {}

    def new_states():
        return [{"count": 0, "total": 0, "best": None} for _ in aggregates]

    if not keys:
        groups[()] = new_states()
    for row in source.rows:
        key = tuple(evaluate(expr, row, []) for expr in keys)
        if key not in groups:
            groups[key] = new_states()
        for spec, state in zip(aggregates, groups[key]):
            value = evaluate(spec["argument"], row, []) if spec["argument"] is not None else None
            _accumulate(state, spec["function"], value, star=spec["argument"] is None)
    rows = []
    for key, states in groups.items():
        values = tuple(_finish(state, spec["function"]) for spec, state in zip(aggregates, states))
        rows.append(key + values)
    columns = [{"name": f"group_{index}", "type": expr["value_type"]}
               for index, expr in enumerate(keys)]
    columns.extend({"name": f"aggregate_{index}", "type": spec["value_type"]}
                   for index, spec in enumerate(aggregates))
    return Relation(columns, rows)


def _accumulate(state, function, value, *, star=False):
    """聚合状态独立于组数，可直接测试宽整数边界。"""
    if not star and value is None:
        return
    state["count"] = checked_query_integer(state["count"] + 1)
    if function == "SUM":
        total = state["total"] + value
        state["total"] = checked_query_integer(total) if type(total) is int else total
        if type(total) is float and not math.isfinite(total):
            raise ExecuteError("浮点聚合超出范围")
    elif function == "AVG":
        # 中间和使用 Python 整数，避免平均值仍合法却因求和溢出而失败。
        state["total"] += value
    elif function == "MIN":
        state["best"] = value if state["best"] is None else min(state["best"], value)
    elif function == "MAX":
        state["best"] = value if state["best"] is None else max(state["best"], value)
    elif function != "COUNT":
        raise ExecuteError(f"unsupported aggregate function '{function}'")


def _finish(state, function):
    if function == "COUNT":
        return state["count"]
    if state["count"] == 0:
        return None
    if function == "SUM":
        return state["total"]
    if function == "AVG":
        try:
            result = state["total"] / state["count"]
        except OverflowError as exc:
            raise ExecuteError("query float out of range") from exc
        if not math.isfinite(result):
            raise ExecuteError("query float out of range")
        return result
    return state["best"]
