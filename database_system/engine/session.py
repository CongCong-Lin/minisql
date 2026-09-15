"""统一数据库会话：事务、恢复、授权和跨进程可见性。"""

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import struct

from sql_compiler import parser
from sql_compiler.ast_nodes import ControlStmt, CreateTableStmt
from sql_compiler.errors import CompileError, ExecuteError
from storage.journal import UndoJournal
from storage.locking import DatabaseLock
from storage.v2 import V2PageStore, V2Storage, V2Catalog
from utils.results import ExecuteResult, StmtResult


def connect(data_dir, *, username=None, password=None, timeout=5, capacity=64, policy="LRU"):
    """打开本地第二版数据库；用户名和密码不进入 SQL 流水线。"""
    return DatabaseSession(data_dir, username=username, password=password,
                           timeout=timeout, capacity=capacity, policy=policy)


class DatabaseSession:
    def __init__(self, data_dir, *, username=None, password=None, timeout=5, capacity=64, policy="LRU"):
        root = Path(data_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.lock = DatabaseLock(root, timeout)
        self.journal = UndoJournal(root / "minisql.undo")
        self.state = "idle"
        self.explicit = False
        self.readonly = False
        self.closed = False
        self.username = None
        self.credential_version = None
        self.pages = self.storage = self.catalog = None
        self.notice = None
        self.lock.acquire()
        stream = None
        try:
            path = root / "minisql.db"
            stream = path.open("r+b" if path.exists() else "w+b", buffering=0)
            self.journal.recover(stream)
            new = os.fstat(stream.fileno()).st_size == 0
            if not new:
                stream.seek(0)
                header = stream.read(6)
                if len(header) != 6 or header[:4] != b"MSQL":
                    raise ExecuteError("数据库文件头损坏")
                version = struct.unpack_from("<H", header, 4)[0]
                if version == 1:
                    raise ExecuteError("[MIGRATION_REQUIRED] 旧库需使用 python -m tools.migrate 迁移到新目录")
                if version != 2:
                    raise ExecuteError("数据库格式版本不受支持")
            if new:
                self.journal.begin(stream)
            self.pages = V2PageStore(stream, self.journal, capacity, policy)
            self.storage = V2Storage(self.pages)
            self.catalog = V2Catalog(self.storage)
            self.catalog._session = self.storage._session = self
            if new:
                self.storage.save_metadata()
                self.pages.flush_all()
                self.journal.commit(stream)
            self.pages.writable = False
            from engine.security import authenticate
            authenticate(self, username, password)
        except Exception:
            if stream is not None:
                try:
                    if self.journal.stream is not None and not self.journal.commit_started:
                        self.journal.recover(stream)
                finally:
                    self.journal.detach()
                    stream.close()
            raise
        finally:
            self.lock.release()

    def _start(self, readonly, explicit=False):
        if self.closed:
            raise ExecuteError("会话已经关闭")
        if self.state != "idle":
            raise ExecuteError("不能嵌套事务")
        self.lock.acquire(readonly)
        try:
            if self.journal.pending():
                if readonly:
                    self.lock.release()
                    self.lock.acquire()
                self.journal.recover(self.pages._file)
                if readonly:
                    self.lock.release()
                    self.lock.acquire(True)
                    if self.journal.pending():
                        self.lock.release()
                        return self._start(readonly, explicit)
            self.pages.refresh()
            self.storage.reload()
            self.catalog.reload()
            from engine.security import validate_identity
            validate_identity(self)
            self.pages.writable = not readonly
            if not readonly:
                self.journal.begin(self.pages._file)
            self.state, self.readonly, self.explicit = "active", readonly, explicit
        except Exception:
            self.pages.writable = False
            self.journal.detach()
            self.lock.release()
            raise

    def begin(self, read_only=False):
        with self.lock.guard:
            self._start(read_only, True)

    def _release(self):
        self.pages.writable = False
        self.state, self.explicit, self.readonly = "idle", False, False
        self.lock.release()

    def commit(self):
        with self.lock.guard:
            if self.state != "active":
                raise ExecuteError("[TRANSACTION_STATE] 没有可提交事务，失败事务必须回滚")
            try:
                if not self.readonly:
                    self.storage.save_metadata()
                    self.pages.flush_all()
                    self.notice = self.journal.commit(self.pages._file)
            except Exception as exc:
                if self.journal.commit_started:
                    committed = self.journal.committed
                    self._poison()
                    if committed:
                        self.notice = "提交成功，会话已关闭，重新连接后继续"
                        return
                    raise ExecuteError("[COMMIT_UNKNOWN] 提交结果未知，请重新连接恢复") from exc
                self.rollback()
                raise ExecuteError(f"[IO] 提交失败，事务已回滚：{exc}") from exc
            self._release()

    def rollback(self):
        with self.lock.guard:
            if self.state not in {"active", "failed"}:
                raise ExecuteError("没有可回滚事务")
            try:
                if not self.readonly:
                    self.journal.recover(self.pages._file)
                self.pages.refresh()
                self.storage.reload()
                self.catalog.reload()
            except Exception as exc:
                self._poison()
                raise ExecuteError("[RECOVERY_FAILED] 回滚失败，日志已保留，请重新连接") from exc
            self._release()

    def _poison(self):
        self.state = "broken"
        self.closed = True
        try:
            self.journal.detach()
        finally:
            try:
                self.pages.close()
            finally:
                self.lock.release()

    @contextmanager
    def transaction(self, readonly=False):
        """供目录读取和管理接口复用；不提前结束调用者的显式事务。"""
        with self.lock.guard:
            own = self.state == "idle"
            if own:
                self._start(readonly, explicit=True)
            elif self.state != "active" or not readonly and self.readonly:
                raise ExecuteError("事务状态不允许当前操作")
            try:
                yield
                if own:
                    self.commit()
            except Exception:
                if own and not self.closed and self.state in {"active", "failed"}:
                    self.rollback()
                elif not self.closed:
                    self.state = "failed"
                raise

    def catalog_snapshot(self):
        with self.transaction(True):
            from engine.security import visible_tables
            result = self.catalog.snapshot()
            visible = visible_tables(self)
            result._tables = {name: entry for name, entry in result._tables.items() if name in visible}
            return result

    def inspect(self, text):
        """授权后在快照上编译，编辑器检查不创建用户事务。"""
        from engine import runtime
        from engine.security import authorize
        from studio.inspect import _from_error, _diagnostic
        with self.transaction(True):
            working = self.catalog.snapshot()
            diagnostics = []
            for segment in runtime._scan_and_segment(text):
                try:
                    if segment.lexer_errors:
                        raise segment.lexer_errors[0]
                    stmt = parser.parse(runtime._parse_tokens(segment))[0]
                    authorize(self, stmt)
                    compiled, error, stmt = runtime._compile_segment(segment, working)
                    if error:
                        diagnostics.extend(_from_error(text, error, compiled.tokens))
                    elif isinstance(stmt, CreateTableStmt):
                        working.create_table(stmt.table, stmt.columns)
                except CompileError as exc:
                    diagnostics.extend(_from_error(text, exc, None))
                except ExecuteError as exc:
                    token = segment.tokens[0] if segment.tokens else segment.global_eof
                    diagnostics.append(_diagnostic(text, "AUTH", token.line, token.column, str(exc), None))
            return diagnostics

    def run(self, text):
        from engine import runtime
        from engine.security import authorize
        from engine.executor import execute
        from engine.physical import choose_plan, execute_control
        with self.lock.guard:
            if self.closed:
                raise ExecuteError("会话已经关闭")
            results = []
            for segment in runtime._scan_and_segment(text):
                token_snapshot = None if segment.lexer_errors else [runtime._token_dict(t) for t in runtime._parse_tokens(segment)]
                compiled = StmtResult(False, None, token_snapshot, None, [], None, None)
                try:
                    if segment.lexer_errors:
                        from sql_compiler.errors import LexerError
                        raise LexerError(0, 0, "", errors=list(segment.lexer_errors), tokens=runtime._parse_tokens(segment))
                    stmt = parser.parse(runtime._parse_tokens(segment))[0]
                    if self.state == "failed" and not (isinstance(stmt, ControlStmt) and stmt.action == "Rollback"):
                        raise ExecuteError("[TRANSACTION_STATE] 当前事务失败，必须 ROLLBACK")
                    control = isinstance(stmt, ControlStmt) and stmt.action in {"Begin", "Commit", "Rollback"}
                    if control:
                        if stmt.action == "Begin":
                            self.begin(stmt.readonly)
                        elif stmt.action == "Commit":
                            self.commit()
                        else:
                            self.rollback()
                        compiled = StmtResult(True, None, [runtime._token_dict(t) for t in runtime._parse_tokens(segment)],
                                              [stmt.to_dict()], [{"op": stmt.action}], ExecuteResult([], "OK", []), True)
                        results.append(compiled)
                        continue
                    from sql_compiler.ast_nodes import SelectStmt
                    readonly = isinstance(stmt, SelectStmt) or isinstance(stmt, ControlStmt) and stmt.action == "Explain"
                    if self.state == "idle":
                        self._start(readonly)
                    elif self.readonly and not readonly:
                        raise ExecuteError("[READ_ONLY] 只读事务不能修改数据库")
                    authorize(self, stmt)
                    compiled, error, _ = runtime._compile_segment(segment, self.catalog)
                    if error:
                        raise error
                    chosen = choose_plan(compiled.plans[1], self.storage)
                    compiled.plans[1] = chosen
                    if isinstance(stmt, ControlStmt):
                        result = execute_control(chosen, self)
                    else:
                        result = execute(chosen, self.catalog, self.storage)
                        if isinstance(stmt, CreateTableStmt):
                            self.storage._entry(stmt.table)["owner"] = self.username
                    compiled.exec_result = result
                    if not self.explicit:
                        self.commit()
                    results.append(compiled)
                except (CompileError, ExecuteError, OSError, ValueError) as exc:
                    if isinstance(exc, OSError):
                        exc = ExecuteError(f"[IO] 数据库读写失败：{exc}")
                    if not self.closed and self.state in {"active", "failed"}:
                        if self.explicit:
                            self.state = "failed"
                        else:
                            try:
                                self.rollback()
                            except ExecuteError as recovery:
                                exc = recovery
                    if isinstance(exc, CompileError):
                        result = runtime._failed_result(exc, compiled.tokens if compiled else None,
                                                        compiled.ast if compiled else None,
                                                        compiled.semantic_ok if compiled else None,
                                                        compiled.plans if compiled else None)
                    else:
                        result = StmtResult(False, f"[EXECUTE] Error: {exc}", compiled.tokens if compiled else None,
                                            compiled.ast if compiled else None, compiled.plans if compiled else [], None,
                                            compiled.semantic_ok if compiled else None)
                    result.error_code = self._error_code(result.error)
                    results.append(result)
                    if self.closed:
                        break
            return results

    @staticmethod
    def _error_code(message):
        for code in ("COMMIT_UNKNOWN", "RECOVERY_FAILED", "LOCK_TIMEOUT", "TRANSACTION_STATE", "READ_ONLY", "AUTH", "IO"):
            if f"[{code}]" in message:
                return code
        return "SQL_ERROR"

    def close(self):
        with self.lock.guard:
            if self.closed:
                return
            try:
                if self.state in {"active", "failed"}:
                    self.rollback()
            finally:
                self.closed = True
                self.journal.detach()
                self.pages.close()
                self.lock.release()
