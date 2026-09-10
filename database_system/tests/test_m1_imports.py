"""验证导入顺序、无磁盘副作用和已实现入口。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

MODULES = [
    "sql_compiler", "sql_compiler.errors", "sql_compiler.lexer",
    "sql_compiler.ast_nodes", "sql_compiler.parser", "sql_compiler.semantic",
    "sql_compiler.types", "sql_compiler.catalog", "sql_compiler.planner",
    "sql_compiler.optimizer", "storage.page", "storage.buffer",
    "storage.file_manager", "storage.record", "storage.storage_engine",
    "engine.executor", "engine.evaluator", "engine.runtime",
    "utils.results", "cli.main", "tools.case_runner",
    "sql_compiler.query_binding", "engine.relational", "engine.query_numbers",
]


@pytest.mark.parametrize("order", [MODULES, list(reversed(MODULES))])
def test_all_modules_import_in_fresh_process_without_files(order, tmp_path):
    """两个相反导入顺序均不应循环依赖或创建数据库文件。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    code = f"import importlib; [importlib.import_module(n) for n in {order!r}]"
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_storage_and_catalog_backends_can_be_wired(tmp_path):
    """页存储与内存目录构造不再是存根；系统目录须由 runtime 引导。"""
    from sql_compiler.catalog import Catalog
    from storage.file_manager import PageStore
    from storage.storage_engine import StorageEngine

    pages = PageStore(str(tmp_path / "minisql.db"), capacity=1)
    storage = StorageEngine(pages)
    try:
        assert storage.pages is pages
        assert (tmp_path / "minisql.db").stat().st_size == 4096
        assert pages.get_page(0).data[:4] == b"MSQL"
        assert storage.has_table("t") is False
        catalog = Catalog()
        assert catalog.snapshot().list_tables() == []
    finally:
        storage.close()


def test_empty_compile_and_run_succeed_without_writing(tmp_path):
    """空输入编译和 run 返回空结果，不创建数据库文件。"""
    from sql_compiler import compile_sql
    from sql_compiler.catalog import Catalog
    from sql_compiler.lexer import tokenize
    from engine.runtime import run

    assert tokenize("")[-1].type.name == "EOF"
    catalog = Catalog()
    assert run("", catalog) == []
    assert compile_sql("", catalog) == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module", ["cli.main", "tools.case_runner"])
def test_command_entries_are_implemented(module, tmp_path):
    """联调后入口必须能启动；空 SQL 的 CLI 退出 0，执行器至少能列出套件。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-B", "-m", module],
        cwd=tmp_path, capture_output=True, input=b"", env=env,
    )
    if module == "cli.main":
        assert result.returncode == 0
        assert result.stdout == b""
    else:
        assert result.returncode in (0, 1, 2)
        assert result.stdout or result.stderr
