"""验证导入顺序、无磁盘副作用和存根边界。"""

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


def test_backend_construction_only_wires_dependencies(tmp_path):
    """骨架构造可用于依赖注入，但不会伪造加载成功或创建文件。"""
    from sql_compiler.catalog import Catalog
    from storage.file_manager import PageStore
    from storage.storage_engine import StorageEngine

    pages = PageStore(str(tmp_path / "minisql.db"), capacity=1)
    storage = StorageEngine(pages)
    catalog = Catalog(storage=storage)
    assert storage.pages is pages and catalog.storage is storage
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(NotImplementedError):
        pages.get_page(0)
    with pytest.raises(NotImplementedError):
        storage.has_table("t")
    with pytest.raises(NotImplementedError):
        catalog.snapshot()


def test_business_entrypoints_remain_explicit_stubs():
    """M1 不把尚未实现的业务返回为空成功。"""
    from sql_compiler import compile_sql
    from sql_compiler.catalog import Catalog
    from sql_compiler.lexer import tokenize
    from engine.runtime import run

    for action in (lambda: tokenize(""), lambda: run("", Catalog()),
                   lambda: compile_sql("", Catalog())):
        with pytest.raises(NotImplementedError, match="M1"):
            action()


@pytest.mark.parametrize("module", ["cli.main", "tools.case_runner"])
def test_command_stubs_do_not_claim_success(module, tmp_path):
    """入口尚未实现时返回 2，正式输出保持为空。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-B", "-m", module], cwd=tmp_path,
                            env=env, capture_output=True)
    assert result.returncode == 2
    assert result.stdout == b""
    assert b"M1" in result.stderr
