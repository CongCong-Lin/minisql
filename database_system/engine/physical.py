"""教学代价模型与控制语句；统计只影响计划选择，不影响结果。"""

from copy import deepcopy
from datetime import date
import json
import math

from sql_compiler.errors import ExecuteError
from storage.btree import BPlusTree
from utils.results import ExecuteResult


def analyze_table(storage, table):
    entry = storage._entry(table)
    columns = storage.catalog.find_table(table)["columns"]
    values = [[] for _ in columns]
    for _, row in storage.scan_records(table, columns):
        for index, value in enumerate(row):
            values[index].append(value)
    stats = {}
    for index, items in enumerate(values):
        nonnull = [value for value in items if value is not None]
        stats[str(index)] = {"version": entry["version"], "nulls": len(items) - len(nonnull),
                             "distinct": len(set(nonnull)), "min": min(nonnull) if nonnull else None,
                             "max": max(nonnull) if nonnull else None}
    entry["stats"] = stats


def execute_control(plan, session):
    storage, action = session.storage, plan["op"]
    if action == "CreateIndex":
        name, table = plan["name"].lower(), plan["table"].lower()
        if name in storage.meta["indexes"]:
            raise ExecuteError("索引已存在")
        columns = session.catalog.find_table(table)["columns"]
        index = next(i for i, c in enumerate(columns) if c.name.lower() == plan["column_name"].lower())
        spec = {"id": storage.pages.new_object(), "name": plan["name"], "table": table,
                "column": index, "type": columns[index].col_type}
        tree = BPlusTree.create(storage.pages, spec)
        for rid, row in storage.scan_records(table, columns):
            tree.insert(row[index], rid)
        storage.meta["indexes"][name] = spec
        analyze_table(storage, table)
    elif action == "DropIndex":
        spec = storage.meta["indexes"].get(plan["name"].lower())
        if spec is None:
            raise ExecuteError("索引不存在")
        BPlusTree(storage.pages, spec).drop()
        del storage.meta["indexes"][plan["name"].lower()]
    elif action == "Analyze":
        analyze_table(storage, plan["table"])
    elif action == "Explain":
        child = plan["child"]
        logical = plan.get("logical_plan", child)
        text = (json.dumps({"logical_plan": logical, "physical_plan": child}, ensure_ascii=False, allow_nan=False, sort_keys=True)
                if plan["format"] == "JSON" else "逻辑计划\n" + format_plan(logical) + "\n执行计划（估计值）\n" + format_plan(child))
        return ExecuteResult([(text,)], "1 row selected", ["执行计划"])
    else:
        from engine.security import role_command
        role_command(plan, session)
    return ExecuteResult([], "OK", [])


def format_plan(plan, indent=0):
    """CLI 与桌面界面共用的可解释文本树。"""
    parts = ["  " * indent + plan["op"] + (" " + str(plan.get("table") or ""))]
    for key in ("index", "estimated_rows", "estimated_cost", "reason", "candidates"):
        if key in plan:
            parts.append("  " * (indent + 1) + f"{key}: {plan[key]}")
    for edge in ("child", "left", "right"):
        if isinstance(plan.get(edge), dict):
            parts.append(format_plan(plan[edge], indent + 1))
    return "\n".join(parts)


def _conditions(expr, columns):
    """仅接受不会产生运行错误的合取比较，避免索引吞掉原有异常。"""
    if expr.get("node") == "BinaryExpr" and expr["op"] == "AND":
        left, right = _conditions(expr["left"], columns), _conditions(expr["right"], columns)
        return None if left is None or right is None else left + right
    def column(node):
        if node.get("node") == "BoundColumnExpr":
            return node["index"]
        if node.get("node") == "IdentifierExpr":
            return next((i for i, c in enumerate(columns) if c.name.lower() == node["name"].lower()), None)
        return None
    if expr.get("node") == "UnaryExpr" and expr["op"] == "IS NULL":
        index = column(expr["operand"])
        return [(index, "NULL", None)] if index is not None else None
    if expr.get("node") != "BinaryExpr" or expr["op"] not in {"=", "<", "<=", ">", ">="}:
        return None
    left, right, op = expr["left"], expr["right"], expr["op"]
    index = column(left)
    if index is None:
        left, right = right, left
        index = column(left)
        op = {"=": "=", "<": ">", "<=": ">=", ">": "<", ">=": "<="}[op]
    if index is None or right.get("node") != "LiteralExpr" or right["value"] is None:
        return None
    return [(index, op, right["value"])]


def _bounds(conditions, column):
    chosen = [item for item in conditions if item[0] == column]
    if not chosen:
        return None
    result = {"lower": None, "upper": None, "lower_inclusive": True, "upper_inclusive": True,
              "null_only": False, "empty": False}
    for _, op, value in chosen:
        if op == "NULL":
            result["null_only"] = True
        for edge, accepted, inclusive in (("lower", {"=", ">", ">="}, op != ">"),
                                          ("upper", {"=", "<", "<="}, op != "<")):
            if op not in accepted:
                continue
            previous = result[edge]
            stronger = previous is None or (value > previous if edge == "lower" else value < previous)
            if stronger:
                result[edge], result[edge + "_inclusive"] = value, inclusive
            elif value == previous:
                result[edge + "_inclusive"] &= inclusive
    lower, upper = result["lower"], result["upper"]
    result["empty"] = (result["null_only"] and any(op != "NULL" for _, op, _ in chosen)
                        or lower is not None and upper is not None and
                        (lower > upper or lower == upper and not (result["lower_inclusive"] and result["upper_inclusive"])))
    return result


def _selectivity(bounds, entry, spec):
    if bounds["empty"]:
        return 0
    lower, upper = bounds["lower"], bounds["upper"]
    equal = lower is not None and lower == upper
    fallback = 0.1 if equal or bounds["null_only"] else 0.25 if lower is not None and upper is not None else 1 / 3
    stat = entry["stats"].get(str(spec["column"]))
    if not stat or stat["version"] != entry["version"]:
        return fallback
    n = max(1, entry["rows"])
    if bounds["null_only"]:
        return stat["nulls"] / n
    minimum, maximum = stat["min"], stat["max"]
    if minimum is None:
        return 0
    if lower is not None and lower > maximum or upper is not None and upper < minimum:
        return 0
    fraction = 1 - stat["nulls"] / n
    if equal:
        return fraction / max(1, stat["distinct"])
    if spec["type"] == "VARCHAR":
        return fraction * fallback
    def numeric(value):
        return date.fromisoformat(value).toordinal() if spec["type"] == "DATE" else float(value)
    lo, hi = numeric(minimum), numeric(maximum)
    a = max(lo, numeric(lower)) if lower is not None else lo
    b = min(hi, numeric(upper)) if upper is not None else hi
    if spec["type"] in {"INT", "DATE", "BOOL"}:
        if lower is not None and not bounds["lower_inclusive"] and a == numeric(lower):
            a += 1
        if upper is not None and not bounds["upper_inclusive"] and b == numeric(upper):
            b -= 1
        return fraction * max(0, b - a + 1) / (hi - lo + 1)
    if hi == lo:
        return fraction
    scale = max(abs(lo), abs(hi), 1)
    return fraction * max(0, b / scale - a / scale) / (hi / scale - lo / scale)


def choose_plan(plan, storage):
    result = deepcopy(plan)
    if result["op"] == "Explain":
        result["estimation"] = "根据当前事务中的存储统计估算"
        result["logical_plan"] = deepcopy(result["child"])
    elif not storage.meta["indexes"]:
        return result
    def visit(node):
        for edge in ("child", "left", "right"):
            if isinstance(node.get(edge), dict):
                visit(node[edge])
        if node["op"] == "SeqScan":
            entry = storage._entry(node["table"])
            node.update(estimated_rows=entry["rows"], estimated_cost=entry["pages"] + 0.01 * entry["rows"], reason="顺序扫描")
        if node["op"] != "Filter" or node["child"]["op"] != "SeqScan":
            return
        scan = node["child"]
        table = scan["table"]
        columns = storage.catalog.find_table(table)["columns"]
        conditions = _conditions(node["predicate"], columns)
        if conditions is None:
            scan["reason"] = "条件不满足安全索引提取规则"
            return
        entry = storage._entry(table)
        best = scan["estimated_cost"]
        candidates = [{"op": "SeqScan", "cost": best}]
        selected = None
        for spec in storage._indexes(table):
            bounds = _bounds(conditions, spec["column"])
            if bounds is None:
                continue
            count = min(entry["rows"], max(0, math.ceil(entry["rows"] * _selectivity(bounds, entry, spec))))
            leaves = min(spec["leaves"], max(1, math.ceil(count * spec["leaves"] / max(1, spec["entries"]))))
            cost = spec["height"] + max(0, leaves - 1) + 4 * min(entry["pages"], count) + .01 * count
            candidates.append({"op": "IndexScan", "index": spec["name"], "cost": cost, "rows": count})
            if cost < best:
                best = cost
                selected = {**scan, "op": "IndexScan", "index": spec["name"], "bounds": bounds,
                            "estimated_cost": cost, "estimated_rows": count, "reason": "估计成本低于顺序扫描"}
        node["child"] = selected or scan
        node["child"]["candidates"] = candidates
    visit(result)
    return result


def scan_index(plan, storage, columns):
    bounds = dict(plan["bounds"])
    if bounds.pop("empty"):
        return
    spec = storage.meta["indexes"][plan["index"].lower()]
    for _, rid in BPlusTree(storage.pages, spec).scan(**bounds):
        yield rid, storage.get_record(plan["table"], rid, columns)
