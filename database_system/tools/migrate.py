"""把已关闭的旧库迁移到新目录，源库始终保留。"""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

from engine.session import connect
from sql_compiler.catalog import Catalog
from sql_compiler.errors import ExecuteError
from storage.file_manager import PageStore
from storage.storage_engine import StorageEngine


def migrate(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination.exists():
        raise ExecuteError("目标必须是源库之外尚不存在的新目录")
    data_file = source / "minisql.db"
    if not data_file.is_file():
        raise ExecuteError("源目录不存在 minisql.db")
    before = hashlib.sha256(data_file.read_bytes()).digest()
    old = StorageEngine(PageStore(str(data_file)))
    try:
        catalog = Catalog(storage=old)
        exported = [(catalog.find_table(name), list(old.scan_records(name, catalog.find_table(name)["columns"])))
                    for name in catalog.list_tables()]
    finally:
        old.close()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".minisql-migrate-", dir=destination.parent)).resolve()
    session = None
    try:
        session = connect(temporary)
        with session.transaction():
            for schema, rows in exported:
                session.catalog.create_table(schema["name"], schema["columns"])
                for _, row in rows:
                    session.storage.insert_record(schema["name"], row, schema["columns"])
        session.close()
        session = connect(temporary)
        with session.transaction(True):
            for schema, rows in exported:
                actual_schema = session.catalog.find_table(schema["name"])
                actual = [row for _, row in session.storage.scan_records(schema["name"], actual_schema["columns"])]
                if actual_schema != schema or actual != [row for _, row in rows]:
                    raise ExecuteError("迁移后的表结构或记录值校验失败")
        session.close()
        if hashlib.sha256(data_file.read_bytes()).digest() != before:
            raise ExecuteError("迁移期间源库发生变化，请关闭旧客户端后重试")
        os.rename(temporary, destination)
        return {"tables": len(exported), "rows": sum(len(rows) for _, rows in exported), "destination": str(destination)}
    finally:
        if session is not None:
            session.close()
        if temporary.exists() and temporary.parent == destination.parent and temporary.name.startswith(".minisql-migrate-"):
            shutil.rmtree(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description="旧库复制迁移：运行前必须关闭旧客户端")
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args(argv)
    try:
        print(migrate(args.source, args.destination))
        return 0
    except (ExecuteError, OSError) as exc:
        print(f"迁移失败：{exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
