"""用两个独立 Python 进程验证 C 的正常关闭持久化，不依赖 B/D。"""

import os
from pathlib import Path
import subprocess
import sys


def test_json_and_physical_pages_survive_process_restart(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONUTF8"] = "1"
    writer = '''
from sql_compiler.catalog import Catalog
from sql_compiler.ast_nodes import ColumnDef
from storage.file_manager import PageStore
catalog = Catalog("catalog.json")
catalog.create_table("Students", [ColumnDef("Name", "VARCHAR", line=7, column=18)])
pages = PageStore("minisql.db", capacity=1)
first, second = pages.alloc_page(), pages.alloc_page()
page = pages.get_page(first)
page.data[32:38] = "重启".encode("utf-8")
page.dirty = True
pages.free_page(second)
pages.close()
'''
    reader = '''
from sql_compiler.catalog import Catalog
from storage.file_manager import PageStore
catalog = Catalog("catalog.json")
assert catalog.list_tables() == ["Students"]
column = catalog.find_column("STUDENTS", "name")
assert (column.col_type, column.line, column.column) == ("VARCHAR", 7, 18)
pages = PageStore("minisql.db", capacity=1)
assert bytes(pages.get_page(1).data[32:38]).decode("utf-8") == "重启"
assert pages.alloc_page() == 2
assert pages.read_page(2) == bytes(4096)
pages.close()
'''
    for code in (writer, reader):
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path,
                                env=env, capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
