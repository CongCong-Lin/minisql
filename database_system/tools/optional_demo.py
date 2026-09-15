"""在独立目录演示八项扩展，口令仅在进程内随机生成。"""

from pathlib import Path
import secrets
import tempfile

from engine.session import connect
from engine.security import initialize, manage_user
from sql_compiler.errors import ExecuteError


def checked(session, sql):
    results = session.run(sql)
    if not results or not all(result.ok for result in results):
        raise RuntimeError(str([result.error for result in results]))
    return results[-1].exec_result


def main():
    root = Path(__file__).resolve().parents[1]
    directory = root / "data"
    directory.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="optional-demo-", dir=directory))
    db = connect(target, capacity=256)
    reader = None
    try:
        checked(db, (root / "examples" / "optional_extensions.sql").read_text(encoding="utf-8"))
        assert checked(db, "SELECT score FROM showcase WHERE id=1;").rows == [(99.0,)]
        print("空值、五种类型、索引、统计、解释和事务演示通过")
        checked(db, "CREATE TABLE large_table(id INT, padding VARCHAR);")
        db.begin()
        for number in range(300):
            checked(db, f"INSERT INTO large_table(id,padding) VALUES({number},'{('填充' * 35)}');")
        db.commit()
        checked(db, "CREATE INDEX idx_large_id ON large_table(id); ANALYZE large_table;")
        explanation = checked(db, "EXPLAIN SELECT * FROM large_table WHERE id=150;").rows[0][0]
        assert "IndexScan" in explanation
        print(explanation)
        password = secrets.token_urlsafe(24)
        initialize(db, "admin", password)
        manage_user(db, "create", "viewer", password)
        checked(db, "CREATE ROLE readers; GRANT SELECT ON showcase TO readers; GRANT readers TO viewer;")
        reader = connect(target, username="viewer", password=password, timeout=0.1)
        assert len(checked(reader, "SELECT * FROM showcase;").rows) == 6
        denied = reader.run("UPDATE showcase SET score=0;")[0]
        assert not denied.ok and denied.error_code == "AUTH"
        db.begin(read_only=True)
        reader.begin(read_only=True)
        reader.commit()
        db.commit()
        db.begin()
        try:
            reader.begin(read_only=True)
        except ExecuteError as exc:
            assert "LOCK_TIMEOUT" in str(exc)
        else:
            raise AssertionError("写事务应阻止其他会话读入")
        db.rollback()
        checked(db, "REVOKE SELECT ON showcase FROM readers;")
        assert not reader.run("SELECT * FROM showcase;")[0].ok
        print("共享读、独占写、授权和长期会话撤权检查通过")
    finally:
        if reader is not None:
            reader.close()
        db.close()
    reopened = connect(target, username="admin", password=password)
    try:
        assert checked(reopened, "SELECT score FROM showcase WHERE id=1;").rows == [(99.0,)]
    finally:
        reopened.close()
    print(f"重开校验通过。演示目录：{target}")
    print("演示账号使用一次性随机密码，不输出密码；交互演示请创建自己的新库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
