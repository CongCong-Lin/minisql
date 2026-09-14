"""真实命令行在不同区域设置下严格读取 UTF-8 输入。"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cli.main import main


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    "CREATE TABLE t(id INT,name VARCHAR);"
    "INSERT INTO t(id,name) VALUES(1,'张三');"
    "SELECT * FROM t;"
)


def _cli(data_dir, payload=b"", *, io_encoding=None, sql_file=None):
    """关闭 UTF-8 模式；保留原生区域设置或强制模拟 GBK 管道。"""
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "0"
    environment.pop("PYTHONIOENCODING", None)
    if io_encoding is not None:
        environment["PYTHONIOENCODING"] = io_encoding
    command = [sys.executable, "-X", "utf8=0", "-m", "cli.main"]
    if sql_file is not None:
        command.append(str(sql_file))
    command.extend([
        "--data-dir", str(data_dir), "--no-tokens", "--no-ast",
        "--no-plan", "--no-opt-plan",
    ])
    return subprocess.run(
        command, cwd=ROOT, env=environment, input=payload,
        capture_output=True, timeout=30,
    )


def _rows(completed):
    """结果必须能严格解码为 UTF-8，且保留原始姓名。"""
    output = completed.stdout.decode("utf-8")
    errors = completed.stderr.decode("utf-8")
    assert completed.returncode == 0, errors + output
    assert errors == ""
    return [json.loads(line[5:]) for line in output.splitlines()
            if line.startswith("ROW: ")]


@pytest.mark.parametrize("io_encoding", [None, "gbk", "gbk:replace", "utf-8:replace"])
def test_utf8_stdin_preserves_chinese_and_persists(tmp_path, io_encoding):
    created = _cli(tmp_path, SQL.encode("utf-8"), io_encoding=io_encoding)
    assert _rows(created) == [[1, "张三"]]
    reopened = _cli(tmp_path, b"SELECT * FROM t;", io_encoding=io_encoding)
    assert _rows(reopened) == [[1, "张三"]]


@pytest.mark.parametrize("io_encoding", [None, "gbk", "gbk:replace", "utf-8:replace"])
def test_invalid_utf8_stdin_is_io_error_before_opening_database(tmp_path, io_encoding):
    data_dir = tmp_path / "database"
    completed = _cli(
        data_dir, b"CREATE TABLE t(id INT);\xff", io_encoding=io_encoding,
    )
    assert completed.returncode == 2
    assert completed.stdout == b""
    errors = completed.stderr.decode("utf-8")
    assert errors.startswith("[IO] ")
    assert "utf-8" in errors
    assert "Traceback" not in errors
    assert not data_dir.exists()


def test_utf8_stdin_accepts_bom(tmp_path):
    completed = _cli(tmp_path, SQL.encode("utf-8-sig"), io_encoding="gbk")
    assert _rows(completed) == [[1, "张三"]]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_utf8_file_with_chinese_path_ignores_stream_encoding(tmp_path, encoding):
    path = tmp_path / "演示语句.sql"
    path.write_text(SQL, encoding=encoding)
    completed = _cli(tmp_path / "database", io_encoding="gbk", sql_file=path)
    assert _rows(completed) == [[1, "张三"]]


def test_stringio_standard_streams_remain_usable(tmp_path, monkeypatch):
    stdin, stdout, stderr = io.StringIO(SQL), io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    assert main([
        "--data-dir", str(tmp_path), "--no-tokens", "--no-ast",
        "--no-plan", "--no-opt-plan",
    ]) == 0
    assert 'ROW: [1,"张三"]' in stdout.getvalue()
    assert stderr.getvalue() == ""
    assert not stdin.closed and not stdout.closed and not stderr.closed
