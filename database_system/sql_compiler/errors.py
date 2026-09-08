"""D 负责：全项目唯一的公共异常类型。"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sql_compiler.lexer import Token


class CompileError(Exception):
    """保存编译阶段、全文件位置和错误原因。"""

    def __init__(self, stage: str, line: int, column: int, reason: str) -> None:
        """初始化一条编译诊断。"""
        self.stage, self.line, self.column, self.reason = stage, line, column, reason
        super().__init__(reason)

    def __str__(self) -> str:
        """返回契约中的单条错误格式。"""
        return f"[{self.stage}] Error at line {self.line}, column {self.column}: {self.reason}"


class LexerError(CompileError):
    """保存单条词法诊断或完整扫描后的聚合异常。"""

    def __init__(self, line: int, column: int, reason: str, *,
                 errors: list[LexerError] | None = None,
                 tokens: list[Token] | None = None) -> None:
        """保留诊断和有效 Token，聚合位置取第一条诊断。"""
        self.errors = list(errors) if errors is not None else []
        self.tokens = list(tokens) if tokens is not None else None
        if self.errors:
            first = self.errors[0]
            line, column, reason = first.line, first.column, first.reason
        super().__init__("LEXER", line, column, reason)


class ParserError(CompileError):
    """语法阶段错误。"""

    def __init__(self, line: int, column: int, reason: str) -> None:
        """初始化语法诊断。"""
        super().__init__("PARSER", line, column, reason)


class SemanticError(CompileError):
    """保存单条或聚合语义诊断。"""

    def __init__(self, line: int, column: int, reason: str, *,
                 errors: list[SemanticError] | None = None) -> None:
        """初始化语义诊断，聚合位置取第一条诊断。"""
        self.errors = list(errors) if errors is not None else []
        if self.errors:
            first = self.errors[0]
            line, column, reason = first.line, first.column, first.reason
        super().__init__("SEMANTIC", line, column, reason)


class PlannerError(CompileError):
    """计划构造或优化阶段错误。"""

    def __init__(self, line: int, column: int, reason: str) -> None:
        """初始化计划诊断。"""
        super().__init__("PLANNER", line, column, reason)


class ExecuteError(Exception):
    """执行及存储错误，不属于编译异常体系。"""

    def __init__(self, message: str) -> None:
        """保存没有源码位置的执行错误。"""
        self.message = message
        super().__init__(message)


class IntegerArithmeticError(Exception):
    """供类型辅助函数、优化器和执行器使用的内部算术异常。"""

    def __init__(self, reason: str) -> None:
        """保存算术失败原因，由调用阶段转换或保留。"""
        self.reason = reason
        super().__init__(reason)
