"""单列非唯一 B+ 树；节点独立读写，避免缓存淘汰后的失效引用。"""

import base64
from bisect import bisect_left, bisect_right
import json
import struct

from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from storage.record import serialize, deserialize
from storage.v2 import END

MAX_KEYS = 8
MIN_LEAF = 4
MIN_CHILDREN = 5


def comparable(entry):
    value, rid = entry
    return value is not None, value if value is not None else 0, tuple(rid)


class BPlusTree:
    def __init__(self, pages, spec):
        self.pages = pages
        self.spec = spec
        self.columns = [ColumnDef("key", spec["type"], line=1, column=1)]

    @classmethod
    def create(cls, pages, spec):
        tree = cls(pages, spec)
        spec.update(root=tree._new(True), entries=0, height=1)
        return tree

    def _new(self, leaf):
        page_id = self.pages.alloc_page()
        node = {"leaf": leaf, "keys": [], "children": [], "next": END}
        self._save(page_id, node)
        self.spec["pages"] = self.spec.get("pages", 0) + 1
        if leaf:
            self.spec["leaves"] = self.spec.get("leaves", 0) + 1
        return page_id

    def _save(self, page_id, node):
        encoded = {**node, "keys": [[base64.b64encode(serialize((key,), self.columns)).decode("ascii"), list(rid)]
                                     for key, rid in node["keys"]]}
        payload = json.dumps(encoded, separators=(",", ":"), allow_nan=False).encode("ascii")
        if len(payload) > 4088:
            raise ExecuteError("索引节点容量溢出")
        data = bytearray(4096)
        tag = ((3 if node["leaf"] else 2) << 30) | self.spec["id"]
        struct.pack_into("<II", data, 0, tag, len(payload))
        data[8:8 + len(payload)] = payload
        page = self.pages.get_page(page_id)
        page.data[:] = data
        page.dirty = True

    def _load(self, page_id):
        data = self.pages.get_page(page_id).data
        tag, size = struct.unpack_from("<II", data)
        if tag & ((1 << 30) - 1) != self.spec["id"] or tag >> 30 not in {2, 3} or size > 4088:
            raise ExecuteError("索引页归属或格式非法")
        try:
            node = json.loads(bytes(data[8:8 + size]))
            node["keys"] = [(deserialize(base64.b64decode(k, validate=True), self.columns)[0], tuple(rid))
                            for k, rid in node["keys"]]
            if node["leaf"] != (tag >> 30 == 3):
                raise ValueError
            if type(node["leaf"]) is not bool or len(node["keys"]) > MAX_KEYS:
                raise ValueError
            for _, rid in node["keys"]:
                if len(rid) != 2 or any(type(part) is not int for part in rid) or rid[0] <= 1 or rid[1] < 0:
                    raise ValueError
            if not isinstance(node["children"], list) or any(type(child) is not int or child <= 1 or child >= self.pages._page_count for child in node["children"]):
                raise ValueError
            if type(node["next"]) is not int or node["next"] != END and not 1 < node["next"] < self.pages._page_count:
                raise ValueError
            if node["leaf"] and node["children"]:
                raise ValueError
            if any(comparable(a) >= comparable(b) for a, b in zip(node["keys"], node["keys"][1:])):
                raise ValueError
            if not node["leaf"] and len(node["children"]) != len(node["keys"]) + 1:
                raise ValueError
            return node
        except (ValueError, TypeError, KeyError) as exc:
            raise ExecuteError("索引节点损坏") from exc

    def _minimum(self, page_id):
        seen = set()
        while True:
            if page_id in seen:
                raise ExecuteError("索引页循环")
            seen.add(page_id)
            node = self._load(page_id)
            if node["leaf"]:
                if not node["keys"]:
                    raise ExecuteError("索引非根子树为空")
                return node["keys"][0]
            page_id = node["children"][0]

    def _separators(self, node):
        if not node["leaf"]:
            node["keys"] = [self._minimum(child) for child in node["children"][1:]]

    def insert(self, value, rid):
        entry = (value, tuple(rid))
        split = self._insert(self.spec["root"], entry, set())
        if split is not None:
            root = self._new(False)
            node = {"leaf": False, "keys": [], "children": [self.spec["root"], split], "next": END}
            self._separators(node)
            self._save(root, node)
            self.spec["root"] = root
            self.spec["height"] += 1
        self.spec["entries"] += 1

    def _insert(self, page_id, entry, seen):
        if page_id in seen:
            raise ExecuteError("索引页循环")
        seen.add(page_id)
        node = self._load(page_id)
        keys = [comparable(key) for key in node["keys"]]
        if node["leaf"]:
            position = bisect_left(keys, comparable(entry))
            if position < len(keys) and keys[position] == comparable(entry):
                raise ExecuteError("索引条目重复")
            node["keys"].insert(position, entry)
        else:
            position = bisect_right(keys, comparable(entry))
            split = self._insert(node["children"][position], entry, seen)
            if split is not None:
                node["children"].insert(position + 1, split)
            self._separators(node)
        if len(node["keys"]) <= MAX_KEYS:
            self._save(page_id, node)
            return None
        other_id = self._new(node["leaf"])
        other = {"leaf": node["leaf"], "keys": [], "children": [], "next": END}
        if node["leaf"]:
            middle = len(node["keys"]) // 2
            other["keys"], node["keys"] = node["keys"][middle:], node["keys"][:middle]
            other["next"], node["next"] = node["next"], other_id
        else:
            middle = len(node["children"]) // 2
            other["children"], node["children"] = node["children"][middle:], node["children"][:middle]
            self._separators(node)
            self._separators(other)
        self._save(page_id, node)
        self._save(other_id, other)
        return other_id

    def delete(self, value, rid):
        self._delete(self.spec["root"], (value, tuple(rid)), set())
        root = self._load(self.spec["root"])
        if not root["leaf"] and len(root["children"]) == 1:
            previous = self.spec["root"]
            self.spec["root"] = root["children"][0]
            self.spec["height"] -= 1
            self._free(previous, False)
        self.spec["entries"] -= 1

    def _free(self, page_id, leaf):
        self.pages.free_page(page_id)
        self.spec["pages"] -= 1
        if leaf:
            self.spec["leaves"] -= 1

    def _delete(self, page_id, entry, seen):
        if page_id in seen:
            raise ExecuteError("索引页循环")
        seen.add(page_id)
        node = self._load(page_id)
        keys = [comparable(key) for key in node["keys"]]
        if node["leaf"]:
            index = bisect_left(keys, comparable(entry))
            if index == len(keys) or keys[index] != comparable(entry):
                raise ExecuteError("应删除的索引条目不存在")
            node["keys"].pop(index)
        else:
            index = bisect_right(keys, comparable(entry))
            self._delete(node["children"][index], entry, seen)
            self._rebalance(node, index)
            self._separators(node)
        self._save(page_id, node)

    def _rebalance(self, parent, index):
        children = parent["children"]
        if len(children) < 2:
            return
        child = self._load(children[index])
        field = "keys" if child["leaf"] else "children"
        minimum = MIN_LEAF if child["leaf"] else MIN_CHILDREN
        if len(child[field]) >= minimum:
            return
        sibling_index = index - 1 if index else 1
        sibling = self._load(children[sibling_index])
        if len(sibling[field]) > minimum:
            if sibling_index < index:
                child[field].insert(0, sibling[field].pop())
            else:
                child[field].append(sibling[field].pop(0))
            self._separators(child)
            self._separators(sibling)
            self._save(children[index], child)
            self._save(children[sibling_index], sibling)
            return
        left_index, right_index = sorted((index, sibling_index))
        left = child if left_index == index else sibling
        right = sibling if right_index == sibling_index else child
        left[field].extend(right[field])
        if left["leaf"]:
            left["next"] = right["next"]
        self._separators(left)
        self._save(children[left_index], left)
        removed = children.pop(right_index)
        self._free(removed, right["leaf"])

    def scan(self, lower=None, upper=None, lower_inclusive=True, upper_inclusive=True, null_only=False):
        page_id, seen = self.spec["root"], set()
        search = (lower, (-1, -1))
        while True:
            if page_id in seen:
                raise ExecuteError("索引页循环")
            seen.add(page_id)
            node = self._load(page_id)
            if node["leaf"]:
                break
            index = bisect_right([comparable(k) for k in node["keys"]], comparable(search)) if lower is not None else 0
            page_id = node["children"][index]
        seen = set()
        while page_id != END:
            if page_id in seen:
                raise ExecuteError("索引叶链循环")
            seen.add(page_id)
            node = self._load(page_id)
            for value, rid in node["keys"]:
                if null_only:
                    if value is not None:
                        return
                else:
                    if value is None:
                        continue
                    if lower is not None and (value < lower or value == lower and not lower_inclusive):
                        continue
                    if upper is not None and (value > upper or value == upper and not upper_inclusive):
                        return
                yield value, rid
            page_id = node["next"]

    def drop(self):
        pending, seen = [self.spec["root"]], set()
        while pending:
            page_id = pending.pop()
            if page_id in seen:
                raise ExecuteError("索引页循环")
            seen.add(page_id)
            node = self._load(page_id)
            pending.extend(node["children"])
            self._free(page_id, node["leaf"])
