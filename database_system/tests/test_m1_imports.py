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


def test_c_backends_are_real_while_b_storage_remains_explicit_stub(tmp_path):
    """C 已能建页和创建快照，B 未实现时不能假装目录已加载。"""
    from sql_compiler.catalog import Catalog
    from storage.file_manager import PageStore
    from storage.storage_engine import StorageEngine

    pages = PageStore(str(tmp_path / "minisql.db"), capacity=1)
    storage = StorageEngine(pages)
    try:
        assert storage.pages is pages
        assert (tmp_path / "minisql.db").stat().st_size == 4096
        assert pages.get_page(0).data[:4] == b"MSQL"
        catalog = Catalog()
        assert catalog.snapshot().list_tables() == []
        with pytest.raises(NotImplementedError):
            storage.has_table("t")
        with pytest.raises(NotImplementedError):
            Catalog(storage=storage)
    finally:
        pages.close()


def test_business_entrypoints_remain_explicit_stubs():
    """尚未接入的下游入口仍明确报告存根，Lexer 已可独立使用。"""
    from sql_compiler import compile_sql
    from sql_compiler.catalog import Catalog
    from sql_compiler.lexer import tokenize
    from engine.runtime import run

    assert tokenize("")[-1].type.name == "EOF"
    for action in (lambda: run("", Catalog()),
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
    if module == "cli.main":
        assert b"M1" in result.stderr
    else:
        assert b"SQL" in result.stderr or b"expected" in result.stderr
