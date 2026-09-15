"""综合验收演示：迁移旧库 → 启用认证 → 创建角色 → 事务更新 → 跨客户端查询
→ 索引与计划 → 模拟崩溃恢复。全程使用独立目录和一次性随机口令。"""

import base64
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import zlib

from engine.security import initialize, manage_user
from engine.session import connect
from sql_compiler.errors import ExecuteError
from tools.migrate import migrate

ROOT = Path(__file__).resolve().parents[1]

CRASH_CODE = """
import os, sys
from engine.session import connect
session = connect(sys.argv[1], username=sys.argv[2], password=sys.argv[3])


def crash(event):
    if event == "数据已同步":
        os._exit(71)


session.journal.hook = crash
session.run("UPDATE records SET label='崩溃未提交' WHERE id=7;")
raise SystemExit(0)
"""


def checked(session, sql):
    results = session.run(sql)
    if not results or not all(result.ok for result in results):
        raise RuntimeError(str([result.error for result in results]))
    return results[-1].exec_result


def one(session, sql):
    return checked(session, sql).rows[0][0]


def build_legacy(source):
    """从仓库固件还原一个真实的旧格式库，旧入口应当拒绝直接打开。"""
    fixture = json.loads((ROOT / "tests/fixtures/v1_database.json").read_text(encoding="utf-8"))
    source.mkdir(parents=True)
    (source / "minisql.db").write_bytes(zlib.decompress(base64.b64decode(fixture["zlib_base64"])))
    return fixture


def main():
    scratch = Path(tempfile.mkdtemp(prefix="acceptance-", dir=ROOT / "data"))
    legacy, migrated = scratch / "legacy", scratch / "migrated"
    admin_password = secrets.token_urlsafe(24)

    build_legacy(legacy)
    try:
        connect(legacy)
    except ExecuteError as exc:
        assert "MIGRATION_REQUIRED" in str(exc), exc
    else:
        raise AssertionError("旧格式库应由新入口要求迁移")
    print("1. 迁移：旧入口正确要求迁移，源库未被改动")

    report = migrate(legacy, migrated)
    assert (legacy / "minisql.db").read_bytes()[:6] == b"MSQL\x01\x00"
    db = connect(migrated, capacity=256)
    try:
        assert checked(db, "SELECT * FROM legacy ORDER BY id;").rows == [
            (1, "原有记录", 10), (2, "另一条", 20)]
        print(f"2. 迁移：{report['tables']} 张表、{report['rows']} 行迁移后取值一致，旧库保持 v1 格式")

        initialize(db, "admin", admin_password)
        manage_user(db, "create", "alice", admin_password)
        manage_user(db, "create", "bob", admin_password)
        with db.transaction():
            db.run("CREATE TABLE records(id INT, label VARCHAR);")
            padding = "填充" * 35
            for number in range(300):
                checked(db, f"INSERT INTO records(id,label) VALUES({number},'{padding}{number}');")
        checked(db, "CREATE ROLE readers; GRANT SELECT ON records TO readers; GRANT readers TO bob;")
        checked(db, "CREATE ROLE builders; GRANT CREATE ON DATABASE TO builders; GRANT builders TO alice;")
        for permission in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            checked(db, f"GRANT {permission} ON records TO builders;")
        print("3. 认证与角色：启用认证后新建 readers（只读）与 builders（可写、可建表）两个角色")

        writer = connect(migrated, username="alice", password=admin_password, timeout=5)
        reader = connect(migrated, username="bob", password=admin_password, timeout=5)
        try:
            extra = writer.run("CREATE TABLE extra(id INT);")[0]
            assert extra.ok, extra.error
            assert not reader.run("UPDATE records SET label='越权';")[0].ok
            assert not reader.run("SELECT * FROM extra;")[0].ok
            print("4. 权限边界：builders 可建表、readers 越权写入与读取他人表均被拒绝")

            writer.begin()
            checked(writer, "UPDATE records SET label='事务提交值' WHERE id=7;")
            assert one(writer, "SELECT label FROM records WHERE id=7;") == "事务提交值"
            writer.commit()
            print("5. 事务更新：显式事务内修改后提交")
        finally:
            writer.close()
            reader.close()

        reader = connect(migrated, username="bob", password=admin_password)
        try:
            assert one(reader, "SELECT label FROM records WHERE id=7;") == "事务提交值"
            print("6. 跨客户端：另一客户端读到已提交的新值")
        finally:
            reader.close()

        before = checked(db, "SELECT * FROM records;").rows
        checked(db, "CREATE INDEX idx_records_id ON records(id); ANALYZE records;")
        plan = one(db, "EXPLAIN SELECT * FROM records WHERE id=7;")
        selected = plan.split("执行计划", 1)[1]
        assert "IndexScan records" in selected, plan
        assert checked(db, "SELECT * FROM records;").rows == before
        print("7. 索引与计划：EXPLAIN 选定 IndexScan 而非 SeqScan，且被解释语句未执行、数据不变")
        print(plan)

        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-c", CRASH_CODE, str(migrated), "alice", admin_password],
            cwd=ROOT, capture_output=True, timeout=60)
        assert completed.returncode == 71, completed.stderr.decode("utf-8", "replace")
        assert one(db, "SELECT label FROM records WHERE id=7;") == "事务提交值"
        print("8. 崩溃恢复：写事务提交前进程被强制结束，重开后数据回滚到已提交版本")
    finally:
        db.close()

    reopened = connect(migrated, username="admin", password=admin_password)
    try:
        assert one(reopened, "SELECT label FROM records WHERE id=7;") == "事务提交值"
        assert len(checked(reopened, "SELECT * FROM legacy ORDER BY id;").rows) == 2
    finally:
        reopened.close()
    print(f"综合验收通过。演示目录：{scratch}")
    print("口令为一次性随机值且不输出；交互演示请使用 tools.studio 或 cli.main 新建目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
