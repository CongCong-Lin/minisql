"""第二版数据库的真实文件、事务、索引和权限回归。"""

import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import zlib

import pytest

from engine.session import connect
from engine.security import initialize, manage_user
from sql_compiler.ast_nodes import ColumnDef
from sql_compiler.errors import ExecuteError
from storage.btree import BPlusTree
from storage.record import serialize, deserialize

ROOT = Path(__file__).resolve().parents[1]


def ok(session, sql):
    results = session.run(sql)
    assert results and all(r.ok for r in results), [r.error for r in results]
    return results[-1].exec_result


@pytest.fixture
def db(tmp_path):
    session = connect(tmp_path)
    yield session
    session.close()


def test_types_null_and_reopen(db):
    ok(db, "CREATE TABLE t(id INT NOT NULL, f FLOAT, b BOOL, d DATE, s VARCHAR);")
    ok(db, "INSERT INTO t(id,f,b,d,s) VALUES(1,2,TRUE,DATE '2024-02-29',NULL);")
    ok(db, "INSERT INTO t(id,f,b,d,s) VALUES(2,NULL,FALSE,NULL,'中文');")
    assert ok(db, "SELECT * FROM t WHERE s IS NULL;").rows == [(1, 2.0, True, "2024-02-29", None)]
    assert ok(db, "SELECT * FROM t WHERE NULL=NULL;").rows == []
    assert ok(db, "SELECT id FROM t WHERE b OR NULL ORDER BY id;").rows == [(1,)]
    assert ok(db, "SELECT COUNT(f),SUM(f),AVG(f) FROM t;").rows == [(1, 2.0, 2.0)]
    ok(db, "UPDATE t SET f=f+0.5 WHERE id=1;")
    assert not db.run("INSERT INTO t(id,f,b,d,s) VALUES(NULL,1,TRUE,NULL,NULL);")[0].ok
    root = db.root
    db.close()
    reopened = connect(root)
    try:
        assert ok(reopened, "SELECT f FROM t WHERE id=1;").rows == [(2.5,)]
    finally:
        reopened.close()


@pytest.mark.parametrize("kind,value", [("FLOAT", float("inf")), ("FLOAT", True), ("BOOL", 1),
                                        ("DATE", "2023-02-29"), ("INT", 1.5)])
def test_record_rejects_bad_type(kind, value):
    with pytest.raises(ExecuteError):
        serialize((value,), [ColumnDef("a", kind, line=1, column=1)])


def test_transactions_failure_and_ddl_rollback(db):
    ok(db, "CREATE TABLE t(id INT);")
    ok(db, "BEGIN; INSERT INTO t(id) VALUES(1); CREATE TABLE undone(id INT);")
    assert ok(db, "SELECT * FROM t;").rows == [(1,)]
    result = db.run("INSERT INTO t(id) VALUES('bad'); COMMIT;")
    assert all(not r.ok for r in result)
    assert db.state == "failed"
    ok(db, "ROLLBACK;")
    assert ok(db, "SELECT * FROM t;").rows == []
    assert db.catalog.find_table("undone") is None
    ok(db, "BEGIN; INSERT INTO t(id) VALUES(2); COMMIT;")
    assert ok(db, "SELECT * FROM t;").rows == [(2,)]


def test_cross_session_refresh_and_shared_locks(db):
    other = connect(db.root, timeout=.1)
    try:
        ok(db, "CREATE TABLE t(id INT);")
        assert ok(other, "SELECT * FROM t;").rows == []
        ok(db, "BEGIN READ ONLY;")
        ok(other, "BEGIN READ ONLY; SELECT * FROM t; COMMIT;")
        failed = other.run("INSERT INTO t(id) VALUES(1);")[0]
        assert not failed.ok and "LOCK_TIMEOUT" in failed.error
        ok(db, "COMMIT;")
        ok(other, "INSERT INTO t(id) VALUES(1);")
        assert ok(db, "SELECT * FROM t;").rows == [(1,)]
    finally:
        other.close()


@pytest.mark.parametrize("capacity", [1, 2, 64])
def test_btree_splits_merges_duplicates_and_rollback(tmp_path, capacity):
    db = connect(tmp_path, capacity=capacity)
    try:
        ok(db, "CREATE TABLE t(id INT, s VARCHAR);")
        with db.transaction():
            columns = db.catalog.find_table("t")["columns"]
            rows = [(None if i % 13 == 0 else i % 23, "x" * 150) for i in range(130)]
            for row in rows:
                db.storage.insert_record("t", row, columns)
        ok(db, "CREATE INDEX ix ON t(id);")
        with db.transaction(True):
            tree = BPlusTree(db.pages, db.storage.meta["indexes"]["ix"])
            found = [db.storage.get_record("t", rid, columns) for _, rid in tree.scan()]
            assert Counter(found) == Counter(r for r in rows if r[0] is not None)
            assert len(list(tree.scan(null_only=True))) == 10
            assert tree.spec["height"] >= 2
        ok(db, "BEGIN; UPDATE t SET s='" + "长" * 80 + "'; DELETE FROM t WHERE id<8; ROLLBACK;")
        assert Counter(ok(db, "SELECT * FROM t;").rows) == Counter(rows)
        ok(db, "UPDATE t SET s='" + "长" * 80 + "';")
        with db.transaction(True):
            tree = BPlusTree(db.pages, db.storage.meta["indexes"]["ix"])
            assert all(db.storage.get_record("t", rid, columns)[1] == "长" * 80 for _, rid in tree.scan())
        ok(db, "DELETE FROM t;")
        with db.transaction(True):
            spec = db.storage.meta["indexes"]["ix"]
            assert spec["entries"] == 0 and spec["height"] == 1
        ok(db, "DROP INDEX ix;")
    finally:
        db.close()


def test_index_cost_and_explain_no_write(db):
    ok(db, "CREATE TABLE t(id INT, s VARCHAR);")
    with db.transaction():
        columns = db.catalog.find_table("t")["columns"]
        for i in range(300):
            db.storage.insert_record("t", (i, "x" * 200), columns)
    ok(db, "CREATE INDEX ix ON t(id); ANALYZE t;")
    explanation = ok(db, "EXPLAIN FORMAT JSON SELECT * FROM t WHERE id=100;").rows[0][0]
    assert "IndexScan" in explanation
    assert ok(db, "SELECT id FROM t WHERE id=100;").rows == [(100,)]
    broad = ok(db, "EXPLAIN SELECT * FROM t WHERE id>=0;").rows[0][0]
    assert "SeqScan" in broad
    before = ok(db, "SELECT COUNT(*) FROM t;").rows
    ok(db, "EXPLAIN DELETE FROM t; EXPLAIN INSERT INTO t(id,s) VALUES(500,'x'); EXPLAIN UPDATE t SET id=7;")
    assert ok(db, "SELECT COUNT(*) FROM t;").rows == before
    assert not db.run("SELECT * FROM t WHERE 1/0=1 AND id=999;")[0].ok


def test_permissions_all_entrypoints_and_revocation(db):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    initialize(db, "admin", "admin-password")
    manage_user(db, "create", "reader", "reader-password")
    ok(db, "CREATE ROLE readers; GRANT SELECT ON t TO readers; GRANT readers TO reader;")
    with pytest.raises(ExecuteError, match="AUTH"):
        connect(db.root)
    reader = connect(db.root, username="reader", password="reader-password")
    try:
        assert ok(reader, "SELECT * FROM t;").rows == [(1,)]
        assert not reader.run("UPDATE t SET id=2;")[0].ok
        assert not reader.run("EXPLAIN DELETE FROM t;")[0].ok
        assert reader.inspect("DELETE FROM t;")
        assert reader.catalog_snapshot().list_tables() == ["t"]
        ok(db, "REVOKE SELECT ON t FROM readers;")
        assert not reader.run("SELECT * FROM t;")[0].ok
        assert reader.catalog_snapshot().list_tables() == []
        from engine.runtime import _compile_sql
        with pytest.raises(Exception):
            _compile_sql("SELECT * FROM t;", reader.catalog)
    finally:
        reader.close()


def test_migration_preserves_fixture_and_maximum_record(tmp_path):
    from tools.migrate import migrate
    fixture = json.loads((ROOT / "tests/fixtures/v1_database.json").read_text(encoding="utf-8"))
    source = tmp_path / "old"
    source.mkdir()
    raw = zlib.decompress(base64.b64decode(fixture["zlib_base64"]))
    (source / "minisql.db").write_bytes(raw)
    with pytest.raises(ExecuteError, match="MIGRATION_REQUIRED"):
        connect(source)
    result = migrate(source, tmp_path / "new")
    assert result["rows"] > 0
    assert (source / "minisql.db").read_bytes() == raw
    fresh = connect(tmp_path / "new")
    try:
        assert ok(fresh, "SELECT * FROM legacy;").rows
    finally:
        fresh.close()


@pytest.mark.parametrize("event,committed", [("旧页已同步", False), ("数据页已写入", False),
                                           ("数据已同步", False), ("提交已同步", True)])
def test_process_crash_recovery(db, event, committed):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    code = """
import os, sys
from engine.session import connect
s=connect(sys.argv[1])
def crash(event):
    if event == sys.argv[2]: os._exit(71)
s.journal.hook=crash
s.run('UPDATE t SET id=2;')
"""
    completed = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(db.root), event], cwd=ROOT,
                               capture_output=True, timeout=30)
    assert completed.returncode == 71, completed.stderr
    assert ok(db, "SELECT * FROM t;").rows == [(2 if committed else 1,)]


def test_more_than_old_table_limit(db):
    ok(db, "BEGIN;")
    for i in range(65):
        ok(db, f"CREATE TABLE t{i}(id INT);")
    ok(db, "COMMIT;")
    assert len(db.catalog_snapshot().list_tables()) == 65


def test_injected_log_failure_blocks_overwrite(db, monkeypatch):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    original = db.journal.before_write
    def fail_before(data, page_id):
        raise OSError("注入日志写入失败")
    monkeypatch.setattr(db.journal, "before_write", fail_before)
    assert not db.run("UPDATE t SET id=2;")[0].ok
    monkeypatch.setattr(db.journal, "before_write", original)
    assert ok(db, "SELECT * FROM t;").rows == [(1,)]


@pytest.mark.parametrize("left,right,and_value,or_value", [
    ("TRUE", "NULL", None, True), ("FALSE", "NULL", False, None),
    ("NULL", "TRUE", None, True), ("NULL", "FALSE", False, None),
    ("NULL", "NULL", None, None),
    ("TRUE", "TRUE", True, True), ("TRUE", "FALSE", False, True),
    ("FALSE", "TRUE", False, True), ("FALSE", "FALSE", False, False),
])
def test_null_logic_with_optimization(db, left, right, and_value, or_value):
    ok(db, "CREATE TABLE t(b BOOL); INSERT INTO t(b) VALUES(TRUE);")
    for operator, expected in (("AND", and_value), ("OR", or_value)):
        sql = f"UPDATE t SET b={left} {operator} {right}; SELECT b FROM t;"
        assert ok(db, sql).rows == [(expected,)]
        assert ok(db, f"SELECT b FROM t WHERE {left} {operator} {right};").rows == ([(expected,)] if expected is True else [])


@pytest.mark.parametrize("kind,values", [
    ("INT", [1, 4, 2, None, 4]), ("FLOAT", [1.25, 0.5, 2.5, None, 0.5]),
    ("BOOL", [True, False, None, True]), ("DATE", ["2024-02-29", "2025-01-01", None, "2024-02-29"]),
    ("VARCHAR", ["\x01" * 255, "中文", None, "'quoted'", "中文"]),
])
def test_index_every_type_and_restart(tmp_path, kind, values):
    db = connect(tmp_path)
    try:
        ok(db, f"CREATE TABLE t(k {kind});")
        columns = db.catalog.find_table("t")["columns"]
        with db.transaction():
            for value in values * 12:
                db.storage.insert_record("t", (value,), columns)
        ok(db, "CREATE INDEX ix ON t(k);")
    finally:
        db.close()
    db = connect(tmp_path)
    try:
        with db.transaction(True):
            tree = BPlusTree(db.pages, db.storage.meta["indexes"]["ix"])
            assert Counter(v for v, _ in tree.scan()) == Counter(v for v in values * 12 if v is not None)
            assert len(list(tree.scan(null_only=True))) == 12
        ok(db, "DELETE FROM t;")
        with db.transaction(True):
            assert db.storage.meta["indexes"]["ix"]["entries"] == 0
    finally:
        db.close()


def test_maximum_old_row_migration(tmp_path):
    from storage.file_manager import PageStore
    from storage.storage_engine import StorageEngine
    from sql_compiler.catalog import Catalog
    from tools.migrate import migrate
    source = tmp_path / "old"
    source.mkdir()
    storage = StorageEngine(PageStore(str(source / "minisql.db")))
    storage.create_table("__catalog__")
    catalog = Catalog(storage=storage)
    columns = [ColumnDef(f"c{i}", "VARCHAR", line=1, column=1) for i in range(16)]
    row = tuple(["x" * 255] * 15 + ["y" * 218])
    assert len(serialize(row, columns)) == 4076
    catalog.create_table("wide", columns)
    storage.insert_record("wide", row, columns)
    storage.close()
    migrate(source, tmp_path / "new")
    db = connect(tmp_path / "new")
    try:
        assert ok(db, "SELECT * FROM wide;").rows == [row]
    finally:
        db.close()


def test_log_cleanup_failure_does_not_undo_commit(db, monkeypatch):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    original = db.journal.clear
    def fail():
        raise OSError("注入清理失败")
    monkeypatch.setattr(db.journal, "clear", fail)
    ok(db, "UPDATE t SET id=2;")
    monkeypatch.setattr(db.journal, "clear", original)
    assert ok(db, "SELECT * FROM t;").rows == [(2,)]


def test_commit_unknown_closes_without_flushing(db, monkeypatch):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    sync = db.journal._sync
    def fail_commit(stream):
        if db.journal.commit_started:
            raise OSError("注入提交同步失败")
        sync(stream)
    monkeypatch.setattr(db.journal, "_sync", fail_commit)
    result = db.run("UPDATE t SET id=2;")[0]
    assert not result.ok and result.error_code == "COMMIT_UNKNOWN" and db.closed
    reopened = connect(db.root)
    try:
        assert ok(reopened, "SELECT * FROM t;").rows in [[(1,)], [(2,)]]
    finally:
        reopened.close()


def test_new_transaction_cannot_reuse_old_commit(db, monkeypatch):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    original = db.journal.clear
    monkeypatch.setattr(db.journal, "clear", lambda: (_ for _ in ()).throw(OSError("清理失败")))
    ok(db, "UPDATE t SET id=2;")
    monkeypatch.setattr(db.journal, "clear", original)
    code = """
import os,sys
from engine.session import connect
s=connect(sys.argv[1])
s.journal.hook=lambda event: os._exit(72) if event=='数据已同步' else None
s.run('UPDATE t SET id=3;')
"""
    completed = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(db.root)], cwd=ROOT, capture_output=True, timeout=30)
    assert completed.returncode == 72
    assert ok(db, "SELECT * FROM t;").rows == [(2,)]


def test_multi_process_serial_writers_and_readonly(db):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(0);")
    code = """
import sys
from engine.session import connect
s=connect(sys.argv[1])
for i in range(12):
    results=s.run('BEGIN; UPDATE t SET id=id+1; COMMIT;')
    assert all(r.ok for r in results),[r.error for r in results]
s.close()
"""
    processes = [subprocess.Popen([sys.executable, "-X", "utf8", "-c", code, str(db.root)], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        _, error = process.communicate(timeout=30)
        assert process.returncode == 0, error
    assert ok(db, "SELECT * FROM t;").rows == [(24,)]
    ok(db, "BEGIN READ ONLY;")
    assert not db.run("UPDATE t SET id=0;")[0].ok
    ok(db, "ROLLBACK;")


def test_bootstrap_failure_releases_lock(tmp_path, monkeypatch):
    from storage.v2 import V2Storage
    original = V2Storage.save_metadata
    monkeypatch.setattr(V2Storage, "save_metadata", lambda _: (_ for _ in ()).throw(OSError("引导写入失败")))
    with pytest.raises(OSError):
        connect(tmp_path)
    monkeypatch.setattr(V2Storage, "save_metadata", original)
    db = connect(tmp_path)
    db.close()


def test_cli_rolls_back_unfinished_transaction(tmp_path):
    completed = subprocess.run([sys.executable, "-X", "utf8", "-m", "cli.main", "--data-dir", str(tmp_path)],
                               input="BEGIN; CREATE TABLE undone(id INT);", text=True, encoding="utf-8",
                               capture_output=True, cwd=ROOT, timeout=30)
    assert completed.returncode == 1 and "TRANSACTION_STATE" in completed.stderr
    db = connect(tmp_path)
    try:
        assert db.catalog_snapshot().list_tables() == []
    finally:
        db.close()


def test_corrupt_empty_catalog_is_rejected(tmp_path):
    import struct
    db = connect(tmp_path)
    db.close()
    with (tmp_path / "minisql.db").open("r+b") as stream:
        stream.seek(4096 + 4)
        stream.write(struct.pack("<HH", 0, 16))
    with pytest.raises(ExecuteError, match="系统目录"):
        connect(tmp_path)


def test_io_error_has_environment_error_code(db, monkeypatch):
    ok(db, "CREATE TABLE t(id INT);")
    def fail(*args):
        raise OSError("注入读取失败")
    monkeypatch.setattr(db.storage, "scan_records", fail)
    result = db.run("SELECT * FROM t;")[0]
    assert not result.ok and result.error_code == "IO"
    assert db.state == "idle"


def test_compiler_explain_marks_missing_statistics():
    from sql_compiler import compile_sql
    from sql_compiler.catalog import Catalog
    catalog = Catalog()
    catalog.create_table("t", [ColumnDef("id", "INT", line=1, column=1)])
    result = compile_sql("EXPLAIN SELECT * FROM t;", catalog)
    assert "估算不可用" in str(result)


def test_recovery_can_restart_after_crash_during_restore(db):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
    writer = """
import os,sys
from engine.session import connect
s=connect(sys.argv[1],capacity=1)
s.journal.hook=lambda event: os._exit(73) if event=='数据已同步' else None
s.run('UPDATE t SET id=9;')
"""
    recovery = """
import os,sys
from engine.session import connect
from storage.journal import UndoJournal
UndoJournal._event=lambda self,event: os._exit(74) if event=='恢复页已写入' else None
connect(sys.argv[1])
"""
    for script, code in [(writer, 73), (recovery, 74)]:
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", script, str(db.root)],
                                cwd=ROOT, capture_output=True, timeout=20)
        assert result.returncode == code, result.stderr
    assert ok(db, "SELECT * FROM t;").rows == [(1,)]


def test_process_shared_read_and_release_after_termination(db):
    ok(db, "CREATE TABLE t(id INT);")
    writer = connect(db.root, timeout=0.05)
    code = """
import sys
from engine.session import connect
s=connect(sys.argv[1])
s.begin(read_only=True)
print('READY',flush=True)
sys.stdin.readline()
s.close()
"""
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code, str(db.root)], cwd=ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b"READY"
        db.begin(read_only=True)
        with pytest.raises(ExecuteError, match="LOCK_TIMEOUT"):
            writer.begin()
        db.commit()
        process.kill()
        process.communicate(timeout=15)
        writer.begin()
        writer.rollback()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)
        writer.close()


def test_index_statistics_and_grants_rollback_together(db):
    ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1); CREATE INDEX ix ON t(id);")
    initialize(db, "admin", "测试密码12345")
    before = json.loads(json.dumps(db.storage.meta, ensure_ascii=False))
    ok(db, "BEGIN; DROP INDEX ix; UPDATE t SET id=2; ANALYZE t; CREATE ROLE readers; GRANT SELECT ON t TO readers; ROLLBACK;")
    assert db.storage.meta == before
    assert ok(db, "SELECT * FROM t;").rows == [(1,)]


def test_migration_failure_keeps_source_and_does_not_publish(tmp_path, monkeypatch):
    from tools.migrate import migrate
    from storage.v2 import V2Storage
    fixture = json.loads((ROOT / "tests/fixtures/v1_database.json").read_text(encoding="utf-8"))
    source = tmp_path / "old"
    source.mkdir()
    raw = zlib.decompress(base64.b64decode(fixture["zlib_base64"]))
    (source / "minisql.db").write_bytes(raw)
    def fail(*args):
        raise OSError("注入迁移失败")
    monkeypatch.setattr(V2Storage, "insert_record", fail)
    with pytest.raises(OSError, match="注入迁移失败"):
        migrate(source, tmp_path / "new")
    assert (source / "minisql.db").read_bytes() == raw
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".minisql-migrate-*"))


def test_context_transaction_keeps_sql_atomic(db):
    with db.transaction():
        ok(db, "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES(1);")
        assert db.state == "active"
    with pytest.raises(RuntimeError):
        with db.transaction():
            ok(db, "UPDATE t SET id=2;")
            raise RuntimeError("主动撤销整组操作")
    assert ok(db, "SELECT * FROM t;").rows == [(1,)]


def test_index_results_equal_forced_sequence_scan(db):
    from copy import deepcopy
    from engine.executor import execute
    ok(db, "CREATE TABLE t(id INT, v INT, pad VARCHAR);")
    with db.transaction():
        columns = db.catalog.find_table("t")["columns"]
        for i in range(180):
            db.storage.insert_record("t", (i, None if i % 23 == 0 else i % 90, "x" * 220), columns)
    ok(db, "CREATE INDEX ix ON t(v); ANALYZE t;")
    used_index = False
    for condition in ("v=7", "v IS NULL", "v>=4 AND v<6", "v>80", "v=5 AND id>0"):
        sql = f"SELECT id,v FROM t WHERE {condition} ORDER BY id;"
        results = db.run(sql)
        assert results[0].ok
        plan = deepcopy(results[0].plans[1])
        def force(node):
            nonlocal used_index
            if node.get("op") == "IndexScan":
                used_index = True
                node["op"] = "SeqScan"
            for edge in ("child", "left", "right"):
                if isinstance(node.get(edge), dict):
                    force(node[edge])
        force(plan)
        with db.transaction(True):
            sequential = execute(plan, db.catalog, db.storage)
        assert sequential.rows == results[0].exec_result.rows
    assert used_index


def test_grouped_arithmetic_keeps_32_bit_checks(db):
    """契约 §11.4：分组路径的普通整数表达式和 WHERE 一样按 32 位受检。"""
    ok(db, "CREATE TABLE t(a INT); INSERT INTO t(a) VALUES(1); INSERT INTO t(a) VALUES(2);")
    assert "integer arithmetic out of range" in db.run(
        "SELECT * FROM t WHERE a+2147483647>0;")[0].error
    assert "integer arithmetic out of range" in db.run(
        "SELECT a FROM t GROUP BY a HAVING a+2147483647>0;")[0].error
    assert "integer arithmetic out of range" in db.run(
        "SELECT a FROM t GROUP BY a HAVING a*2147483647>0;")[0].error
    assert "division by zero" in db.run(
        "SELECT a FROM t GROUP BY a HAVING a/0>0;")[0].error
    # 聚合结果属于宽整数，仍允许超出 32 位；未溢出的分组条件照常求值。
    assert ok(db, "SELECT COUNT(*) FROM t HAVING COUNT(*)+2147483647>0;").rows == [(2,)]
    assert ok(db, "SELECT a FROM t GROUP BY a HAVING a+1>0 ORDER BY a;").rows == [(1,), (2,)]
