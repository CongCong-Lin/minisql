"""D 负责：生命周期与唯一 SQL 提交入口。"""

from copy import deepcopy
from sql_compiler import parser, semantic, planner, optimizer
from sql_compiler.catalog import Catalog
from sql_compiler.lexer import Token
from storage.storage_engine import StorageEngine
from utils.results import StmtResult


def open_database(data_dir: str, *, mode: str = "database"
                  ) -> tuple[Catalog, StorageEngine | None]:
    """按显式模式打开目录及存储，新库先引导系统目录。"""
    raise NotImplementedError("M1 存根：数据库初始化由 D 实现")


def run(text: str, catalog: Catalog,
        storage: StorageEngine | None = None) -> list[StmtResult]:
    """完整扫描并按分号分段，逐段编译、提交或执行，保留全部结果。"""
    raise NotImplementedError("M1 存根：顶层语句编排由 D 实现")


def close_database(catalog: Catalog,
                   storage: StorageEngine | None) -> None:
    """数据库模式关闭存储，编译模式不额外提交目录。"""
    raise NotImplementedError("M1 存根：数据库关闭由 D 实现")


def _compile_sql(text: str, catalog: Catalog) -> list[StmtResult]:
    """实现公开纯编译入口，使用目录快照并在首个失败段抛错。"""
    raise NotImplementedError("M1 存根：纯编译入口由 D 实现")


def _compile_statement(tokens: list[Token], catalog: Catalog) -> StmtResult:
    """连接单语句的四个阶段，供 M1 测试替身验证接口。"""
    token_snapshot = [
        {"type": token.type.name, "lexeme": token.lexeme,
         "line": token.line, "column": token.column}
        for token in tokens
    ]
    stmts = parser.parse(tokens)
    if len(stmts) != 1:
        raise ValueError("内部单语句连接器必须接收恰好一条语句")
    ast_snapshot = [stmt.to_dict() for stmt in stmts]
    annotated = semantic.analyze(stmts, catalog)
    original = planner.plan(annotated[0], catalog)
    original_snapshot = deepcopy(original)
    optimized, _rules = optimizer.optimize(original)
    return StmtResult(
        ok=True, error=None, tokens=token_snapshot, ast=ast_snapshot,
        plans=[original_snapshot, deepcopy(optimized)], exec_result=None,
        semantic_ok=True,
    )
