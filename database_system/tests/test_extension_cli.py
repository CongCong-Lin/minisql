"""扩展的命令行协议、旧库兼容、演示与跨进程持久化。"""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zlib

import pytest

from tools.extension_demo import collect_output

ROOT = Path(__file__).resolve().parents[1]


def cli(data_dir, sql, *, mode="database", details=False):
    command = [sys.executable, "-X", "utf8", "-m", "cli.main", "--mode", mode,
               "--data-dir", str(data_dir)]
    if not details:
        command += ["--no-tokens", "--no-ast", "--no-plan", "--no-opt-plan"]
    return subprocess.run(command, cwd=ROOT, input=sql, text=True, encoding="utf-8",
                          capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))


def test_update_persists_and_query_artifacts_are_json(tmp_path):
    created = cli(tmp_path, "CREATE TABLE t(id INT,name VARCHAR); INSERT INTO t(id,name) VALUES(1,'短');")
    assert created.returncode == 0, created.stderr + created.stdout
    updated = cli(tmp_path, "UPDATE t SET id=id+2,name='更长的中文记录';")
    assert updated.returncode == 0 and "1 row updated" in updated.stdout
    reopened = cli(tmp_path, "SELECT a.id,MIN(b.name) AS label FROM t a JOIN t b ON a.id=b.id GROUP BY a.id ORDER BY label;", details=True)
    assert reopened.returncode == 0, reopened.stderr + reopened.stdout
    queries, errors = collect_output(reopened.stdout)
    assert queries == [{"columns": ["a.id", "label"], "rows": [[3, "更长的中文记录"]]}]
    assert errors == []
    sections = {}
    for line in reopened.stdout.splitlines():
        for prefix in ("TOKENS: ", "AST: ", "PLAN: ", "OPT_PLAN: "):
            if line.startswith(prefix):
                sections[prefix] = json.loads(line[len(prefix):])
    assert len(sections) == 4
    assert sections["AST: "][0]["joins"]
    assert sections["PLAN: "]["op"] == "Project"


def test_compiler_update_never_mutates_rows(tmp_path):
    output = cli(tmp_path, "CREATE TABLE t(id INT); UPDATE t SET id=id+1; SELECT COUNT(*) FROM t;", mode="compiler")
    assert output.returncode == 0, output.stderr + output.stdout
    assert "RESULT:" not in output.stdout and "ROW:" not in output.stdout
    assert (tmp_path / "catalog.json").exists()
    assert not (tmp_path / "minisql.db").exists()


def test_empty_aggregate_json_null_and_float(tmp_path):
    output = cli(tmp_path, "CREATE TABLE t(id INT); SELECT COUNT(*),AVG(id) FROM t; INSERT INTO t(id) VALUES(1); INSERT INTO t(id) VALUES(2); SELECT AVG(id) FROM t;")
    assert output.returncode == 0, output.stderr + output.stdout
    queries, errors = collect_output(output.stdout)
    assert queries[0]["rows"] == [[0, None]]
    assert queries[1]["rows"] == [[1.5]]
    assert "null" in output.stdout and errors == []


@pytest.mark.parametrize("details", [False, True])
def test_repeatable_demo_matches_independent_expected_results(tmp_path, details):
    data_dir = tmp_path / "demo"
    command = [sys.executable, "-X", "utf8", "-m", "tools.extension_demo", "--data-dir", str(data_dir)]
    if details:
        command.append("--details")
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr + result.stdout
    assert (data_dir / "minisql.db").is_file()
    assert "全部演示结果已核对" in result.stdout
    log = (data_dir / "execution.log").read_text(encoding="utf-8")
    assert "08_reopen.sql" in log
    if details:
        assert "TOKENS:" in log and "OPT_PLAN:" in log


def test_demo_refuses_existing_directory(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("保留", encoding="utf-8")
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "tools.extension_demo",
                             "--data-dir", str(tmp_path)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2
    assert marker.read_text(encoding="utf-8") == "保留"
    assert list(tmp_path.iterdir()) == [marker]


def test_open_baseline_v1_database_and_update_without_migration(tmp_path):
    fixture = json.loads((ROOT / "tests" / "fixtures" / "v1_database.json").read_text(encoding="utf-8"))
    data = zlib.decompress(base64.b64decode(fixture["zlib_base64"]))
    assert hashlib.sha256(data).hexdigest() == fixture["sha256"]
    (tmp_path / "minisql.db").write_bytes(data)
    before = cli(tmp_path, "SELECT * FROM legacy ORDER BY id;")
    assert before.returncode == 0, before.stderr + before.stdout
    assert collect_output(before.stdout)[0] == [{"columns": ["id", "count", "asc"],
                                               "rows": [[1, "原有记录", 10], [2, "另一条", 20]]}]
    updated = cli(tmp_path, "UPDATE legacy SET count='扩展更新后的记录',asc=asc+1 WHERE id=1;")
    assert updated.returncode == 0, updated.stderr + updated.stdout
    later = cli(tmp_path, "SELECT id,count,asc FROM legacy ORDER BY id;")
    assert collect_output(later.stdout)[0][0]["rows"] == [[1, "扩展更新后的记录", 11], [2, "另一条", 20]]
    assert (tmp_path / "minisql.db").read_bytes()[:6] == data[:6]
