"""可选扩展使用真实编译、目录、存储和执行器进行端到端验证。"""

import pytest

from engine.runtime import close_database, open_database, run


@pytest.fixture
def database(tmp_path):
    catalog, storage = open_database(str(tmp_path))
    try:
        yield catalog, storage
    finally:
        close_database(catalog, storage)


def execute(database, sql):
    results = run(sql, *database)
    assert all(result.ok for result in results), [result.error for result in results]
    return results[-1].exec_result if results else None


@pytest.fixture
def students(database):
    execute(database, """
        CREATE TABLE student(id INT, name VARCHAR, score INT, team VARCHAR);
        INSERT INTO student(id,name,score,team) VALUES(1,'Ada',90,'A');
        INSERT INTO student(id,name,score,team) VALUES(2,'Bob',80,'B');
        INSERT INTO student(id,name,score,team) VALUES(3,'Cora',90,'A');
        INSERT INTO student(id,name,score,team) VALUES(4,'Dan',70,'B');
        INSERT INTO student(id,name,score,team) VALUES(5,'Eve',80,'A');
    """)
    return database


def test_order_multiple_keys_hidden_column_and_stability(students):
    result = execute(students, "SELECT name FROM student ORDER BY score DESC, id DESC;")
    assert result.columns == ["name"]
    assert result.rows == [("Cora",), ("Ada",), ("Eve",), ("Bob",), ("Dan",)]
    assert execute(students, "SELECT id FROM student ORDER BY score DESC;").rows == [
        (1,), (3,), (2,), (5,), (4,)]


def test_order_aliases_and_qualified_columns(students):
    result = execute(students, "SELECT s.name AS who FROM student AS s WHERE s.score >= 80 ORDER BY who DESC;")
    assert result.columns == ["who"]
    assert result.rows == [("Eve",), ("Cora",), ("Bob",), ("Ada",)]
    assert execute(students, "SELECT s.* FROM student s ORDER BY s.id DESC;").rows[0] == (5, "Eve", 80, "A")


def test_order_chinese_empty_result_and_soft_words(database):
    execute(database, """
        CREATE TABLE words(asc INT, count VARCHAR, sum INT);
        INSERT INTO words(asc,count,sum) VALUES(1,'王',3);
        INSERT INTO words(asc,count,sum) VALUES(2,'张',2);
        INSERT INTO words(asc,count,sum) VALUES(3,'李',1);
    """)
    assert execute(database, "SELECT count FROM words ORDER BY count;").rows == [("张",), ("李",), ("王",)]
    result = execute(database, "SELECT count AS label FROM words WHERE asc > 9 ORDER BY sum;")
    assert result.columns == ["label"] and result.rows == []


@pytest.mark.parametrize("sql,stage,reason", [
    ("SELECT id FROM student ORDER BY missing;", "SEMANTIC", "列不存在"),
    ("SELECT id AS score FROM student ORDER BY score;", "SEMANTIC", "歧义"),
    ("SELECT id AS x, score AS X FROM student ORDER BY x;", "SEMANTIC", "别名重复"),
    ("SELECT id FROM student ORDER BY 1;", "PARSER", "expected"),
    ("SELECT id FROM student ORDER BY score + 1;", "PARSER", "expected"),
    ("SELECT missing.id FROM student ORDER BY id;", "SEMANTIC", "列不存在"),
])
def test_order_diagnostics(students, sql, stage, reason):
    result = run(sql, *students)[0]
    assert not result.ok and f"[{stage}]" in result.error and reason in result.error


def test_update_assignments_read_original_row_and_count_matches(students):
    result = execute(students, "UPDATE student SET score = id, id = score;")
    assert result.message == "5 rows updated"
    assert execute(students, "SELECT id,score FROM student ORDER BY score;").rows == [
        (90, 1), (80, 2), (90, 3), (70, 4), (80, 5)]
    assert execute(students, "UPDATE student SET name = name WHERE score = 1;").message == "1 row updated"
    assert execute(students, "UPDATE student SET score = 1 WHERE id = 999;").message == "0 rows updated"


def test_update_later_expression_failure_does_not_modify_earlier_row(students):
    result = run("UPDATE student SET score = 10 / (id - 2);", *students)[0]
    assert not result.ok and "division by zero" in result.error
    assert execute(students, "SELECT score FROM student ORDER BY id;").rows == [(90,), (80,), (90,), (70,), (80,)]
    results = run("UPDATE student SET score = 2147483647 + id; SELECT id FROM student ORDER BY id;", *students)
    assert not results[0].ok and results[1].ok
    assert "out of range" in results[0].error
    assert len(results[0].plans) == 2


@pytest.mark.parametrize("sql,reason", [
    ("UPDATE student SET id=1, ID=2;", "目标列重复"),
    ("UPDATE student SET missing=1;", "列不存在"),
    ("UPDATE student SET score='bad';", "需要 INT"),
    ("UPDATE student SET name=score;", "需要 VARCHAR"),
    ("UPDATE student SET score=SUM(score);", "不能使用聚合"),
    ("UPDATE student SET score=1 WHERE name;", "必须为 BOOL"),
    ("UPDATE student SET score=other.id;", "列不存在"),
    ("UPDATE __catalog__ SET id=1;", "系统目录"),
])
def test_update_semantic_errors(students, sql, reason):
    result = run(sql, *students)[0]
    assert not result.ok and "[SEMANTIC]" in result.error and reason in result.error


def test_update_preflights_total_record_length(database):
    names = [f"c{i}" for i in range(16)]
    execute(database, "CREATE TABLE wide(" + ",".join(name + " VARCHAR" for name in names) + ");")
    execute(database, "INSERT INTO wide(" + ",".join(names) + ") VALUES(" + ",".join("''" for _ in names) + ");")
    assignments = ",".join(name + "='" + "x" * 255 + "'" for name in names)
    result = run("UPDATE wide SET " + assignments + ";", *database)[0]
    assert not result.ok and "4076" in result.error
    assert execute(database, "SELECT * FROM wide;").rows == [("",) * 16]


def test_update_growth_capacity_one_and_restart(tmp_path):
    from sql_compiler.catalog import Catalog
    from storage.file_manager import PageStore
    from storage.storage_engine import StorageEngine

    path = tmp_path / "minisql.db"
    storage = StorageEngine(PageStore(str(path), capacity=1))
    storage.create_table("__catalog__")
    catalog = Catalog(storage=storage)
    database = catalog, storage
    try:
        execute(database, "CREATE TABLE t(id INT, value VARCHAR);")
        columns = catalog.find_table("t")["columns"]
        original = [storage.insert_record("t", (i, "a" * 100), columns) for i in range(50)]
        changed = storage.update_record("t", original[0], (0, "b" * 255), columns)
        assert changed[0] != original[0][0]
        assert storage.update_record("t", changed, (0, "c"), columns) == changed
        result = execute(database, "UPDATE t SET value='" + "z" * 255 + "', id=id+1;")
        assert result.message == "50 rows updated"
        rows = execute(database, "SELECT id,value FROM t ORDER BY id;").rows
        assert rows == [(i, "z" * 255) for i in range(1, 51)]
    finally:
        close_database(catalog, storage)
    reopened = open_database(str(tmp_path))
    try:
        assert execute(reopened, "SELECT id,value FROM t ORDER BY id;").rows == rows
    finally:
        close_database(*reopened)


def test_storage_update_rejects_invalid_rid_without_mutation(database):
    from sql_compiler.errors import ExecuteError

    catalog, storage = database
    execute(database, "CREATE TABLE t(id INT); CREATE TABLE u(id INT);")
    columns = catalog.find_table("t")["columns"]
    rid = storage.insert_record("t", (1,), columns)
    other = storage.insert_record("u", (2,), columns)
    for invalid in (None, (True, 0), (0, 0), (rid[0], -1), (rid[0], 999), other):
        with pytest.raises(ExecuteError):
            storage.update_record("t", invalid, (3,), columns)
    assert list(storage.scan_records("t", columns)) == [(rid, (1,))]
    storage.delete_record("t", rid)
    with pytest.raises(ExecuteError, match="deleted"):
        storage.update_record("t", rid, (3,), columns)


@pytest.fixture
def courses(students):
    execute(students, """
        CREATE TABLE course(student_id INT, title VARCHAR);
        INSERT INTO course(student_id,title) VALUES(1,'Database');
        INSERT INTO course(student_id,title) VALUES(1,'Compiler');
        INSERT INTO course(student_id,title) VALUES(2,'Database');
        INSERT INTO course(student_id,title) VALUES(9,'Missing');
    """)
    return students


def test_join_one_to_many_filter_projection_and_order(courses):
    result = execute(courses, """
        SELECT s.name AS who,c.title FROM student s INNER JOIN course AS c
        ON s.id=c.student_id WHERE s.score>=80 ORDER BY who, c.title;
    """)
    assert result.columns == ["who", "c.title"]
    assert result.rows == [("Ada", "Compiler"), ("Ada", "Database"), ("Bob", "Database")]
    assert execute(courses, "SELECT c.* FROM student s JOIN course c ON s.id=c.student_id;").columns == [
        "c.student_id", "c.title"]


def test_join_many_to_many_keeps_duplicates_and_self_join(database):
    execute(database, """
        CREATE TABLE t(id INT);
        INSERT INTO t(id) VALUES(1); INSERT INTO t(id) VALUES(1);
        INSERT INTO t(id) VALUES(2);
    """)
    result = execute(database, "SELECT * FROM t AS a JOIN t b ON a.id=b.id ORDER BY a.id;")
    assert result.columns == ["a.id", "b.id"]
    assert result.rows == [(1, 1)] * 4 + [(2, 2)]


def test_join_three_tables_and_unambiguous_bare_column(courses):
    execute(courses, """
        CREATE TABLE teacher(title VARCHAR, teacher_name VARCHAR);
        INSERT INTO teacher(title,teacher_name) VALUES('Database','Li');
        INSERT INTO teacher(title,teacher_name) VALUES('Compiler','Wang');
    """)
    result = execute(courses, """
        SELECT name,c.title,teacher_name FROM student s JOIN course c ON s.id=c.student_id
        JOIN teacher t ON c.title=t.title ORDER BY name,teacher_name;
    """)
    assert result.rows == [("Ada", "Database", "Li"), ("Ada", "Compiler", "Wang"), ("Bob", "Database", "Li")]
    assert result.columns == ["s.name", "c.title", "t.teacher_name"]


def test_join_empty_and_non_matching_rows_keep_headers(courses):
    execute(courses, "CREATE TABLE empty(id INT);")
    empty = execute(courses, "SELECT s.name,e.id FROM student s JOIN empty e ON s.id=e.id;")
    assert empty.rows == [] and empty.columns == ["s.name", "e.id"]
    assert execute(courses, "SELECT * FROM empty e JOIN student s ON e.id=s.id;").rows == []
    assert execute(courses, "SELECT s.name FROM student s JOIN course c ON s.id=c.student_id+100;").rows == []


@pytest.mark.parametrize("sql,reason", [
    ("SELECT id FROM student a JOIN student b ON a.id=b.id;", "歧义"),
    ("SELECT * FROM student a JOIN student A ON TRUE;", "别名重复"),
    ("SELECT student.id FROM student s JOIN course c ON s.id=c.student_id;", "列不存在"),
    ("SELECT * FROM student s JOIN course c ON z.id=c.student_id JOIN student z ON TRUE;", "列不存在"),
    ("SELECT * FROM student s JOIN course c ON s.name=c.student_id;", "不支持类型"),
    ("SELECT * FROM student s JOIN course c ON s.id;", "必须为 BOOL"),
    ("SELECT * FROM student s JOIN course c ON COUNT(*)>1;", "不能使用聚合"),
    ("SELECT * FROM student s JOIN __catalog__ c ON TRUE;", "系统目录"),
    ("SELECT * FROM student s JOIN missing c ON TRUE;", "表不存在"),
])
def test_join_semantic_diagnostics(courses, sql, reason):
    result = run(sql, *courses)[0]
    assert not result.ok and "[SEMANTIC]" in result.error and reason in result.error


@pytest.mark.parametrize("join", ["LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN", "NATURAL JOIN"])
def test_outer_and_implicit_join_extensions_are_rejected(courses, join):
    result = run(f"SELECT * FROM student s {join} course c ON s.id=c.student_id;", *courses)[0]
    assert not result.ok and "[PARSER]" in result.error


def test_group_all_aggregates_having_and_alias_order(students):
    result = execute(students, """
        SELECT team,COUNT(*) AS n,SUM(score) AS total,AVG(score) AS mean,
               MIN(name) AS first_name,MAX(score) AS highest
        FROM student GROUP BY team HAVING AVG(score)>80.5 ORDER BY total DESC;
    """)
    assert result.columns == ["team", "n", "total", "mean", "first_name", "highest"]
    assert result.rows == [("A", 3, 260, pytest.approx(260 / 3), "Ada", 90)]
    assert execute(students, "SELECT team,COUNT(name) AS n FROM student GROUP BY team HAVING n>=2 ORDER BY n;").rows == [
        ("B", 2), ("A", 3)]
    assert execute(students, "SELECT team AS label FROM student GROUP BY team HAVING label='A';").rows == [("A",)]


def test_group_multiple_keys_and_group_without_aggregate(students):
    result = execute(students, "SELECT team,score,COUNT(*) FROM student GROUP BY team,score ORDER BY team,score DESC;")
    assert result.rows == [("A", 90, 2), ("A", 80, 1), ("B", 80, 1), ("B", 70, 1)]
    assert execute(students, "SELECT team FROM student GROUP BY team ORDER BY team;").rows == [("A",), ("B",)]
    assert execute(students, "SELECT team FROM student GROUP BY team ORDER BY AVG(score);").rows == [("B",), ("A",)]


def test_global_aggregate_wide_integer_and_duplicate_aggregation(database):
    execute(database, "CREATE TABLE t(value INT); INSERT INTO t(value) VALUES(2147483647); INSERT INTO t(value) VALUES(2147483647);")
    results = run("SELECT SUM(value),SUM(value) AS total,COUNT(value),AVG(value) FROM t HAVING SUM(value)>2147483647 ORDER BY total;", *database)
    assert results[0].ok, results[0].error
    assert results[0].exec_result.rows == [(4294967294, 4294967294, 2, 2147483647.0)]
    node = results[0].plans[0]
    while node["op"] != "Aggregate":
        node = node["child"]
    assert len(node["aggregates"]) == 3


def test_empty_aggregate_null_values_and_three_valued_having(database):
    execute(database, "CREATE TABLE t(value INT, name VARCHAR);")
    result = execute(database, "SELECT COUNT(*),COUNT(value),SUM(value),AVG(value),MIN(name),MAX(value) FROM t;")
    assert result.rows == [(0, 0, None, None, None, None)]
    assert execute(database, "SELECT name,COUNT(*) FROM t GROUP BY name;").rows == []
    assert execute(database, "SELECT COUNT(*) FROM t HAVING SUM(value)>0 OR COUNT(*)=0;").rows == [(0,)]
    assert execute(database, "SELECT COUNT(*) FROM t HAVING NOT(SUM(value)>0);").rows == []
    assert execute(database, "SELECT COUNT(*) FROM t HAVING SUM(value)>0 AND FALSE;").rows == []
    assert execute(database, "SELECT COUNT(*) FROM t HAVING COUNT(*)=0 OR 1/0>0;").rows == [(0,)]


def test_update_join_group_having_sort_combined(courses):
    execute(courses, "UPDATE student SET score=score+2 WHERE id<=2;")
    result = execute(courses, """
        SELECT s.team AS label,COUNT(*) AS n,SUM(s.score) AS total
        FROM student s JOIN course c ON s.id=c.student_id
        GROUP BY s.team HAVING n>0 ORDER BY total DESC;
    """)
    assert result.rows == [("A", 2, 184), ("B", 1, 82)]


@pytest.mark.parametrize("sql,stage,reason", [
    ("SELECT id,COUNT(*) FROM student;", "SEMANTIC", "GROUP BY"),
    ("SELECT team,name FROM student GROUP BY team;", "SEMANTIC", "GROUP BY"),
    ("SELECT team FROM student GROUP BY team HAVING score>0;", "SEMANTIC", "GROUP BY"),
    ("SELECT team FROM student GROUP BY team ORDER BY score;", "SEMANTIC", "GROUP BY"),
    ("SELECT SUM(name) FROM student;", "SEMANTIC", "只接受 INT"),
    ("SELECT AVG(name) FROM student;", "SEMANTIC", "只接受 INT"),
    ("SELECT MIN(*) FROM student;", "SEMANTIC", "只有 COUNT"),
    ("SELECT id FROM student WHERE COUNT(*)>0;", "SEMANTIC", "不能使用聚合"),
    ("SELECT id FROM student HAVING id>0;", "SEMANTIC", "必须用于分组"),
    ("SELECT team,COUNT(*) AS score FROM student GROUP BY team HAVING score>0;", "SEMANTIC", "歧义"),
    ("SELECT COUNT(*) FROM student HAVING SUM(score);", "SEMANTIC", "必须为 BOOL"),
    ("SELECT SUM(AVG(score)) FROM student;", "PARSER", "expected"),
    ("SELECT SUM(score+1) FROM student;", "PARSER", "expected"),
    ("SELECT COUNT(DISTINCT team) FROM student;", "PARSER", "expected"),
    ("SELECT team FROM student GROUP BY 1;", "PARSER", "expected"),
    ("SELECT score+1 FROM student;", "PARSER", "expected"),
])
def test_aggregate_diagnostics(students, sql, stage, reason):
    result = run(sql, *students)[0]
    assert not result.ok and f"[{stage}]" in result.error and reason in result.error


def test_query_numeric_boundaries():
    from engine.query_numbers import QUERY_INT_MAX, QUERY_INT_MIN, checked_query_integer, query_arithmetic
    from engine.relational import _accumulate
    from sql_compiler.errors import ExecuteError

    assert checked_query_integer(QUERY_INT_MIN) == QUERY_INT_MIN
    assert checked_query_integer(QUERY_INT_MAX) == QUERY_INT_MAX
    assert query_arithmetic("/", -3, 2, "BIGINT") == -1
    for value in (QUERY_INT_MIN - 1, QUERY_INT_MAX + 1, True):
        with pytest.raises(ExecuteError):
            checked_query_integer(value)
    with pytest.raises(ExecuteError, match="64-bit"):
        _accumulate({"count": 1, "total": QUERY_INT_MAX, "best": None}, "SUM", 1)
    with pytest.raises(ExecuteError, match="64-bit"):
        _accumulate({"count": QUERY_INT_MAX, "total": 0, "best": None}, "COUNT", None, star=True)
    with pytest.raises(ExecuteError, match="float"):
        query_arithmetic("*", 1e308, 1e308, "FLOAT")


@pytest.mark.parametrize("descending,expected", [(False, [None, 1, 2]), (True, [2, 1, None])])
def test_sort_nullable_intermediate_results(descending, expected):
    from engine.relational import evaluate_relation
    from sql_compiler.ast_nodes import ColumnDef

    # 当前磁盘不保存空值；用行源替身检验查询结果排序的空值规则。
    class Catalog:
        def find_table(self, _table):
            return {"columns": [ColumnDef("value", "INT", line=1, column=1)]}

    class Storage:
        def scan_records(self, _table, _columns):
            return iter([((1, i), (value,)) for i, value in enumerate([2, None, 1])])

    plan = {"op": "Sort", "keys": [{"expr": {"node": "BoundColumnExpr", "index": 0},
                                       "descending": descending}],
            "child": {"op": "SeqScan", "table": "t"}}
    assert [row[0] for row in evaluate_relation(plan, Catalog(), Storage()).rows] == expected


@pytest.mark.parametrize("query", [
    "SELECT s.id FROM student s WHERE 1=1 AND s.score>10+8 ORDER BY s.id;",
    "SELECT s.id,c.title FROM student s JOIN course c ON s.id=c.student_id WHERE TRUE ORDER BY s.id,c.title;",
    "SELECT team,COUNT(*) AS n FROM student GROUP BY team HAVING TRUE AND n>1+0 ORDER BY n;",
    "SELECT COUNT(*) FROM student WHERE FALSE HAVING SUM(score)>0 OR TRUE;",
    "SELECT COUNT(*) FROM student WHERE FALSE HAVING NOT(SUM(score)>0) AND TRUE;",
    "SELECT COUNT(*) FROM student HAVING COUNT(*)>0 OR 1/0>0;",
    "SELECT COUNT(*) FROM student HAVING 1/0>0 AND FALSE;",
    "SELECT COUNT(*) FROM student HAVING 2147483647+1>2147483647;",
])
def test_extended_plan_optimization_preserves_results_and_errors(courses, query):
    from engine.executor import execute as execute_plan
    from sql_compiler import compile_sql
    from sql_compiler.errors import ExecuteError

    result = compile_sql(query, courses[0])[0]
    outcomes = []
    for plan in result.plans:
        try:
            value = execute_plan(plan, *courses)
        except ExecuteError as exc:
            outcomes.append(("error", str(exc)))
        else:
            outcomes.append(("ok", value.rows, value.columns, value.message))
    assert outcomes[0] == outcomes[1]


def test_extensions_compile_without_writing_and_keep_ui_snapshots(students):
    from copy import deepcopy
    from sql_compiler import compile_sql
    from sql_compiler import ast_nodes
    from sql_compiler.semantic import analyze

    sql = """
        UPDATE student SET score=student.score+1;
        SELECT s.team,AVG(s.score) AS mean FROM student s JOIN student t ON s.id=t.id
        GROUP BY s.team HAVING mean>1 ORDER BY mean DESC;
    """
    results = compile_sql(sql, students[0])
    assert all(result.ok and result.exec_result is None for result in results)
    assert execute(students, "SELECT score FROM student WHERE id=1;").rows == [(90,)]

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if isinstance(value, dict):
            return getattr(ast_nodes, value["node"])(**{key: decode(item) for key, item in value.items() if key != "node"})
        return value

    for result in results:
        before = deepcopy(result.ast)
        statements = decode(result.ast)
        analyze(statements, students[0])
        assert [stmt.to_dict() for stmt in statements] == before
        assert result.ast == before


def test_soft_words_survive_catalog_restart(tmp_path):
    database = open_database(str(tmp_path))
    try:
        execute(database, "CREATE TABLE having(on INT, inner INT, asc INT); INSERT INTO having(on,inner,asc) VALUES(1,2,3);")
    finally:
        close_database(*database)
    database = open_database(str(tmp_path))
    try:
        assert execute(database, "SELECT on,inner,asc FROM having ORDER BY inner ASC;").rows == [(1, 2, 3)]
    finally:
        close_database(*database)


def test_growing_update_insert_failure_preserves_original_record(database, monkeypatch):
    from sql_compiler.errors import ExecuteError

    catalog, storage = database
    execute(database, "CREATE TABLE t(value VARCHAR); INSERT INTO t(value) VALUES('x');")
    columns = catalog.find_table("t")["columns"]
    rid, original = next(storage.scan_records("t", columns))

    def fail(*_args):
        raise ExecuteError("模拟写入失败")

    monkeypatch.setattr(storage, "insert_record", fail)
    with pytest.raises(ExecuteError, match="模拟写入失败"):
        storage.update_record("t", rid, ("longer",), columns)
    assert list(storage.scan_records("t", columns)) == [(rid, original)]
