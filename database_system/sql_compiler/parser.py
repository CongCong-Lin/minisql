"""B 负责：递归下降入口，跨分段恢复由 runtime 负责。"""

import math
from sql_compiler.ast_nodes import (
    BinaryExpr, ColumnDef, CreateTableStmt, DeleteStmt, IdentifierExpr,
    InsertStmt, LiteralExpr, SelectStmt, Stmt, UnaryExpr,
)
from sql_compiler.errors import ParserError
from sql_compiler.lexer import Token, TokenType


def parse(tokens: list[Token]) -> list[Stmt]:
    """解析含唯一结束标记的 Token 流，首个语法错误即抛出。"""
    return _Parser(tokens).parse()


class _Parser:
    FIRST_STATEMENT = {"CREATE", "DELETE", "INSERT", "SELECT"}
    FIRST_FACTOR = {"IDENTIFIER", "CONST", "'('"}

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> Token:
        return self.tokens[self.index]

    def advance(self) -> Token:
        token = self.peek()
        self.index += 1
        return token

    def _display(self, token: Token) -> str:
        return "EOF" if token.type is TokenType.EOF else token.lexeme

    def _error(self, expected: set[str] | list[str], token: Token | None = None,
               suffix: str = "") -> None:
        token = token or self.peek()
        shown = " | ".join(sorted(expected))
        reason = f"unexpected token '{self._display(token)}', expected: {shown}"
        if suffix:
            reason += f"; {suffix}"
        raise ParserError(token.line, token.column, reason)

    def expect(self, typ: TokenType, lexeme: str | None = None) -> Token:
        token = self.peek()
        if token.type is not typ or (lexeme is not None and token.lexeme.upper() != lexeme.upper()):
            expected = {lexeme.upper()} if lexeme and typ is TokenType.KEYWORD else {f"'{lexeme}'" if lexeme else typ.name}
            self._error(expected, token)
        return self.advance()

    def match_keyword(self, word: str) -> Token | None:
        token = self.peek()
        if token.type is TokenType.KEYWORD and token.lexeme.upper() == word:
            return self.advance()
        return None

    def parse(self) -> list[Stmt]:
        result = []
        while self.peek().type is not TokenType.EOF:
            if self.peek().type is not TokenType.KEYWORD or self.peek().lexeme.upper() not in self.FIRST_STATEMENT:
                token = self.peek()
                unsupported = token.lexeme.upper() if token.type is TokenType.KEYWORD else None
                self._error({"CREATE", "DELETE", "INSERT", "SELECT"}, token,
                            f"statement not supported: {unsupported}" if unsupported else "")
            result.append(self.statement())
        return result

    def statement(self) -> Stmt:
        word = self.peek().lexeme.upper()
        if word == "CREATE": return self.create_stmt()
        if word == "INSERT": return self.insert_stmt()
        if word == "SELECT": return self.select_stmt()
        return self.delete_stmt()

    def identifier(self) -> Token:
        token = self.peek()
        if token.type is not TokenType.IDENTIFIER:
            self._error({"IDENTIFIER"}, token)
        return self.advance()

    def create_stmt(self) -> CreateTableStmt:
        start = self.expect(TokenType.KEYWORD, "CREATE")
        self.expect(TokenType.KEYWORD, "TABLE")
        table = self.identifier()
        self.expect(TokenType.DELIMITER, "(")
        columns = [self.column_def()]
        while self.peek().type is TokenType.DELIMITER and self.peek().lexeme == ",":
            self.advance(); columns.append(self.column_def())
        self.expect(TokenType.DELIMITER, ")")
        self.expect(TokenType.DELIMITER, ";")
        return CreateTableStmt(table.lexeme, columns, line=start.line, column=start.column)

    def column_def(self) -> ColumnDef:
        name = self.identifier()
        token = self.peek()
        if token.type is not TokenType.KEYWORD or token.lexeme.upper() not in {"INT", "VARCHAR"}:
            self._error({"INT", "VARCHAR"}, token)
        self.advance()
        return ColumnDef(name.lexeme, token.lexeme.upper(), line=name.line, column=name.column)

    def id_list(self) -> list[Token]:
        values = [self.identifier()]
        while self.peek().type is TokenType.DELIMITER and self.peek().lexeme == ",":
            self.advance(); values.append(self.identifier())
        return values

    def insert_stmt(self) -> InsertStmt:
        start = self.expect(TokenType.KEYWORD, "INSERT")
        self.expect(TokenType.KEYWORD, "INTO")
        table = self.identifier()
        self.expect(TokenType.DELIMITER, "(")
        columns = [t.lexeme for t in self.id_list()]
        self.expect(TokenType.DELIMITER, ")")
        self.expect(TokenType.KEYWORD, "VALUES")
        self.expect(TokenType.DELIMITER, "(")
        values = [self.literal()]
        while self.peek().type is TokenType.DELIMITER and self.peek().lexeme == ",":
            self.advance(); values.append(self.literal())
        self.expect(TokenType.DELIMITER, ")")
        self.expect(TokenType.DELIMITER, ";")
        return InsertStmt(table.lexeme, columns, values, line=start.line, column=start.column)

    def select_stmt(self) -> SelectStmt:
        start = self.expect(TokenType.KEYWORD, "SELECT")
        if self.peek().type is TokenType.OPERATOR and self.peek().lexeme == "*":
            self.advance(); columns = "*"
        else:
            columns = [t.lexeme for t in self.id_list()]
        self.expect(TokenType.KEYWORD, "FROM")
        table = self.identifier()
        where = self.where_opt()
        self.expect(TokenType.DELIMITER, ";")
        return SelectStmt(columns, table.lexeme, where, line=start.line, column=start.column)

    def delete_stmt(self) -> DeleteStmt:
        start = self.expect(TokenType.KEYWORD, "DELETE")
        self.expect(TokenType.KEYWORD, "FROM")
        table = self.identifier()
        where = self.where_opt()
        self.expect(TokenType.DELIMITER, ";")
        return DeleteStmt(table.lexeme, where, line=start.line, column=start.column)

    def where_opt(self):
        if self.match_keyword("WHERE"):
            return self.expression()
        return None

    def literal(self) -> LiteralExpr:
        token = self.peek()
        if token.type is TokenType.KEYWORD and token.lexeme.upper() == "NULL":
            self._error({"INTEGER_CONST", "FLOAT_CONST", "STRING_CONST", "TRUE", "FALSE"}, token, "NULL is not supported")
        if token.type is TokenType.KEYWORD and token.lexeme.upper() in {"TRUE", "FALSE"}:
            self.advance(); return LiteralExpr(token.lexeme.upper() == "TRUE", "BOOL", line=token.line, column=token.column)
        if token.type is not TokenType.CONST:
            self._error({"INTEGER_CONST", "FLOAT_CONST", "STRING_CONST", "TRUE", "FALSE"}, token)
        self.advance(); raw = token.lexeme
        if raw.startswith("'"):
            return LiteralExpr(raw[1:-1].replace("''", "'"), "VARCHAR", line=token.line, column=token.column)
        try:
            if "." in raw:
                value = float(raw)
                if not math.isfinite(value): raise ValueError
                return LiteralExpr(value, "FLOAT", line=token.line, column=token.column)
            value = int(raw)
            if not -2147483648 <= value <= 2147483647: raise ValueError
            return LiteralExpr(value, "INT", line=token.line, column=token.column)
        except ValueError:
            reason = "float literal out of range" if "." in raw else "integer literal out of range"
            raise ParserError(token.line, token.column, reason)

    def expression(self): return self.or_expr()
    def or_expr(self):
        left = self.and_expr()
        while self.match_keyword("OR"):
            op = self.tokens[self.index - 1]; left = BinaryExpr("OR", left, self.and_expr(), line=op.line, column=op.column)
        return left
    def and_expr(self):
        left = self.not_expr()
        while self.match_keyword("AND"):
            op = self.tokens[self.index - 1]; left = BinaryExpr("AND", left, self.not_expr(), line=op.line, column=op.column)
        return left
    def not_expr(self):
        if self.match_keyword("NOT"):
            op = self.tokens[self.index - 1]; return UnaryExpr("NOT", self.not_expr(), line=op.line, column=op.column)
        return self.comparison()
    def comparison(self):
        left = self.arith_expr()
        if self.peek().type is TokenType.OPERATOR and self.peek().lexeme in {"=", "!=", ">", ">=", "<", "<="}:
            op = self.advance(); return BinaryExpr(op.lexeme, left, self.arith_expr(), line=op.line, column=op.column)
        return left
    def arith_expr(self):
        left = self.term()
        while self.peek().type is TokenType.OPERATOR and self.peek().lexeme in {"+", "-"}:
            op = self.advance(); left = BinaryExpr(op.lexeme, left, self.term(), line=op.line, column=op.column)
        return left
    def term(self):
        left = self.factor()
        while self.peek().type is TokenType.OPERATOR and self.peek().lexeme in {"*", "/"}:
            op = self.advance(); left = BinaryExpr(op.lexeme, left, self.factor(), line=op.line, column=op.column)
        return left
    def factor(self):
        token = self.peek()
        if token.type is TokenType.IDENTIFIER:
            self.advance(); return IdentifierExpr(token.lexeme, line=token.line, column=token.column)
        if token.type is TokenType.DELIMITER and token.lexeme == "(":
            self.advance(); value = self.expression(); self.expect(TokenType.DELIMITER, ")"); return value
        return self.literal()
