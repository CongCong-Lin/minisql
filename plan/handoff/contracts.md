# 《大型平台软件设计实习》公共契约（v1.6）

> 本文档是四人开发的唯一权威接口标准，适用于 SQL 编译器、页式存储、数据库系统三个阶段。
> 制定日期：2026-09-07；本次修订：2026-09-10。配套：[四人分工](team_plan.md)、[任务交接](handoff.md)。
> v1.5 是基础开发冻结版。v1.6 按用户确认的独立扩展计划实现 UPDATE、排序、内连接、聚合；数据库由当前负责人维护，UI 同伴在独立分支开发后再合并。本次修订不代表原四人会签已经发生。

修订记录：v1.1 补充错误与优化规则；v1.2 补充存储及执行接口；v1.3 增加 `run()`；v1.4 对齐词法条款、补充 Token/AST 结果及审批规则；v1.5 统一异常身份、语句恢复、目录提交时点、表级存储接口、物理布局、系统目录、命令行输出和验收边界。

v1.6 增加查询作用域、列绑定、UPDATE／ORDER BY／INNER JOIN／GROUP BY／HAVING 和查询结果类型，见 §11。旧基础 AST、结果外层字段和磁盘格式保持兼容；§3—§9 中未特别说明的 AST／计划描述为基础形式，扩展形式以 §11 为准。

## 1. 语言与词法规则

### 1.1 保留字与大小写

保留字共 **23 个**：

```text
SELECT FROM WHERE CREATE TABLE INSERT INTO VALUES DELETE
UPDATE SET ORDER BY GROUP JOIN AND OR NOT NULL
INT VARCHAR TRUE FALSE
```

- 全部保留，关键字匹配大小写不敏感；标识符不得与关键字重名。
- UPDATE、SET、ORDER、BY、GROUP、JOIN 识别为保留关键字并支持 §2.1 的扩展语法。NULL 输入仍由 Parser 拒绝，不进入语义阶段。
- AS、INNER、ON、HAVING、ASC、DESC、COUNT、SUM、AVG、MIN、MAX 为上下文词，不增加全局保留字；旧表列名继续合法。
- 表名、列名比较统一使用 `.lower()`。Lexer 和 AST 保留原文；Catalog 保留首次注册的大小写用于展示，物理表映射保存小写名称。执行器查列也按小写比较。
- 字符串内容保持原样，不做大小写归一化。

### 1.2 Token 与公开入口

A 在 `sql_compiler/lexer.py` 定义 Token、TokenType 和入口：

```python
@dataclass
class Token:
    type: TokenType   # KEYWORD、IDENTIFIER、CONST、OPERATOR、DELIMITER、EOF
    lexeme: str       # 原文，字符串包含外层引号和原始转义
    line: int         # 从 1 开始
    column: int       # 从 1 开始，指首个字符

def tokenize(text: str) -> list[Token]: ...
```

TokenType 使用枚举名称区分类别，不为每个关键字或常量另建种别码。INTEGER_CONST、FLOAT_CONST、STRING_CONST 是文法中的常量形态，共用 CONST 类别；Parser 按词素转换。

整个输入扫描完成后，输出恰好一个 `Token(EOF, "", line, column)`。EOF 指向归一化文本最后一个字符之后；空输入为第 1 行第 1 列。发生词法错误时，完整扫描结果通过 §2.2 的聚合异常传递。

### 1.3 识别规则

| 类别 | 规则 |
|---|---|
| 标识符 | `[A-Za-z_][A-Za-z0-9_]*`，最多 64 个 ASCII 字符 |
| 整数 | `[0-9]+` |
| 浮点数 | `[0-9]+\.[0-9]+`，不支持指数或省略小数点两侧数字 |
| 字符串 | 单引号包围，两个连续单引号 `''` 表示字面单引号 |
| 运算符 | `= != > >= < <= + - * /`，不支持 `==` |
| 分隔符 | `( ) , ; .`，点号不改变完整数字词素的识别规则 |

- 注释包括 `--` 到行尾和非嵌套的 `/* ... */`；注释不产生 Token。
- 输入先把 `\r\n`、`\r` 转成 `\n`，再计算位置。空格、制表符均推进一列；换行推进行号并把列号重置为 1。列号按字符计，不按 UTF-8 字节计。
- 字符串不允许裸换行；词素保留外层引号和 `''`，值由 Parser 在 §4.1 转换。
- 注释、字符串中的分号不是分隔符 Token。

### 1.4 词法错误与消耗范围

| 情形 | 诊断要求 | 续扫方式 |
|---|---|---|
| 非法字符，如 `@ # $ ?` | 指出该字符及其位置 | 跳过一个字符 |
| 非法数字，如 `12abc`、`1.2.3` | 指出完整错误词素 | 从数字起点消耗连续的字母、数字、下划线、小数点，作为一条错误 |
| 标识符超过 64 字符 | 指出完整词素 | 消耗完整标识符 |
| `==` | 原因包含 `did you mean '='?` | 消耗这两个字符，不输出两个等号 Token |
| 未闭合字符串 | 指出起始单引号位置 | 跳至当前行尾或文件尾 |
| 未闭合块注释 | 指出起始 `/*` 位置 | 跳至文件尾 |

错误词素不产生正常 Token；被错误字符串或注释吞入的分号也不产生 Token。续扫过程中只收集诊断，扫描完毕后按 §2.2 抛出聚合错误。

## 2. 文法与语句级独立性

### 2.1 权威文法

B 的 `grammar.md` 在 Parser 完成时复制本节 EBNF。代码必须与文法一致；`{}` 表示零或多次，`[]` 表示可选。

```ebnf
program        -> statement_list EOF ;
statement_list -> { statement } ;
statement      -> create_stmt | insert_stmt | select_stmt | delete_stmt | update_stmt ;

create_stmt    -> CREATE TABLE IDENTIFIER '(' column_def { ',' column_def } ')' ';' ;
column_def     -> IDENTIFIER type ;
type           -> INT | VARCHAR ;

insert_stmt    -> INSERT INTO IDENTIFIER '(' id_list ')' VALUES '(' value_list ')' ';' ;
id_list        -> IDENTIFIER { ',' IDENTIFIER } ;
value_list     -> literal { ',' literal } ;
literal        -> INTEGER_CONST | FLOAT_CONST | STRING_CONST | TRUE | FALSE ;

select_stmt    -> SELECT select_list FROM table_ref { join_clause }
                 where_opt group_opt having_opt order_opt ';' ;
select_list    -> select_item { ',' select_item } ;
select_item    -> '*' | IDENTIFIER '.' '*' | select_expr [ AS IDENTIFIER ] ;
select_expr    -> column_ref | aggregate ;
column_ref     -> IDENTIFIER [ '.' IDENTIFIER ] ;
aggregate      -> aggregate_name '(' ( column_ref | '*' ) ')' ;
aggregate_name -> COUNT | SUM | AVG | MIN | MAX ;
table_ref      -> IDENTIFIER [ [ AS ] IDENTIFIER ] ;
join_clause    -> [ INNER ] JOIN table_ref ON expression ;
where_opt      -> [ WHERE expression ] ;
group_opt      -> [ GROUP BY column_ref { ',' column_ref } ] ;
having_opt     -> [ HAVING expression ] ;
order_opt      -> [ ORDER BY order_item { ',' order_item } ] ;
order_item     -> select_expr [ ASC | DESC ] ;

delete_stmt    -> DELETE FROM IDENTIFIER where_opt ';' ;
update_stmt    -> UPDATE IDENTIFIER SET assignment { ',' assignment } where_opt ';' ;
assignment     -> IDENTIFIER '=' expression ;

expression     -> or_expr ;
or_expr        -> and_expr { OR and_expr } ;
and_expr       -> not_expr { AND not_expr } ;
not_expr       -> NOT not_expr | comparison ;
comparison     -> arith_expr [ comp_op arith_expr ] ;
comp_op        -> '=' | '!=' | '>' | '>=' | '<' | '<=' ;
arith_expr     -> term { ('+' | '-') term } ;
term           -> factor { ('*' | '/') factor } ;
factor         -> column_ref | aggregate | literal | '(' expression ')' ;
```

- 基础支持 CREATE TABLE、INSERT、SELECT（含 WHERE）、DELETE，以及算术、TRUE/FALSE、NOT；v1.6 增加 UPDATE、ORDER BY、INNER JOIN、GROUP BY、常用聚合和 HAVING。
- 优先级从高到低：**括号、乘除、加减、比较、NOT、AND、OR**；同级算术左结合。`NOT a = 1` 为 `NOT(a=1)`。
- 不支持一元正负号、NULL 输入、默认值、普通 SELECT 列表算术表达式或 VARCHAR 长度参数。负数可由 `0 - 3` 等表达式产生。SELECT 列表允许列、星号和聚合，聚合参数限列引用，只有 COUNT 允许星号。
- 不支持的保留语法在 Parser 报错。NULL 的附加原因固定包含 `NULL is not supported`；其他扩展语法包含 `statement not supported: <关键字>`，位置指实际遇到的不支持关键字。仍保留 §3.1 的 unexpected/expected 诊断。
- 空文件、空白和完整注释输入合法，返回空列表；单独 `;` 不是空语句，报语法错误。

### 2.2 词法错误的聚合传递

`tokenize()` 的成功返回类型不变。有错时，扫描结束后抛一个聚合 `LexerError`：

- `errors: list[LexerError]` 为非空的单条诊断列表，不包含聚合对象自身；单条诊断的 `errors=[]`、`tokens=None`。
- `tokens: list[Token]` 保存完整输入的有效 Token，末尾恰好一个全局 EOF。
- 聚合异常的 `line/column/reason` 取第一条诊断，兼容单错误位置断言。
- 错误按 `(line, column)` 稳定排序，同位置保持发现顺序。正常路径无错误时直接返回 Token，不返回“成功标志”。

`run()` 从正常返回值或聚合异常中提取 Token 和诊断，不为恢复而重新扫描文本。词法异常的 Token 用于内部恢复；含词法错误的分段对外仍为 `StmtResult.tokens=None`。

### 2.3 分号切片、局部 EOF 与恢复

D 负责以下公共编排规则：

1. 对全局有效 Token 流按 `DELIMITER ";"` 切片，分号保留在当前片段。禁止用原始文本的 `split(";")` 分句。
2. 每条诊断归入其位置所在的片段：上一分号之后、当前分号之前；最后剩余诊断归入尾片段。仅有错误、没有有效 Token 的片段也必须产生失败结果。
3. 分号结尾的片段附一个局部 EOF，位置为该分号后一列；非空尾片段使用全局 EOF 的位置。空且无错误的尾片段不生成结果。
4. 含词法错误的片段整体失败，不进入 Parser；其余片段调用 `parse()`。Parser 首个语法错误即抛出，内部不做错误恢复。
5. `run()` 保存该片段的成功或失败结果，继续下一片段。每次 Parser 输入和每条 `StmtResult.tokens` 分别包含一个局部 EOF，不修改全局流的唯一 EOF。

分号是唯一语句结束符，最后一条也必须有分号。缺分号时，Parser 在当前 lookahead（下一条看似新语句的首 Token 或 EOF）报告 `expected: ';'`。例如：

```sql
SELECT * FROM t
SELECT * FROM t2;
SELECT * FROM t3;
```

前两行属于同一分段，在第 2 行 SELECT 处报缺分号，整个分段失败，t2 的查询不执行；第 3 行独立处理。所有位置沿用全文件坐标，不能按片段重新从第 1 行计数。

## 3. Parser 与 AST

### 3.1 递归下降及诊断集合

`parse(tokens: list[Token]) -> list[Stmt]` 使用递归下降；基础分支采用单 Token lookahead，聚合调用与限定星号使用有限的额外前瞻。输入允许多个完整语句；第一个语法错误即抛 `ParserError`。跨错误继续执行是 `run()` 的职责。

- `peek()` 不消费，`advance()` 消费，`expect(type, lexeme?)` 失败时不消费当前 Token。
- 非终结符入口无法选择分支：expected 取该非终结符的 FIRST 集。
- 产生式中途 `expect()` 失败：expected 取正在匹配的终结符，例如 `SELECT * t;` 期待 FROM，语句尾期待 `';'`。
- FIRST 集从统一的终结符集合和产生式引用、并集推导；允许在 Parser 中声明文法对应集合，不要求实现 EBNF 解析器。禁止在各报错分支复制手写诊断列表。
- expected 展示项用大写关键字、终结符名称以及单引号包围的标点，按展示字符串字典序排列，以 ` | ` 连接。
- 关键字匹配统一用 `match_keyword("SELECT")`，内部比较 Token 类别及 `lexeme.upper()`。
- 常规语法错误原因格式为 `unexpected token '<原文>', expected: <集合>`；EOF 显示为 `EOF`。专项原因在其后追加 `; <原因>`。保留字出现在表名或列名位置时，位置指该保留字 Token。

### 3.2 AST 字段与序列化

B 在 `sql_compiler/ast_nodes.py` 定义以下节点。字段名是公开契约；所有节点都有 `line/column`，语句取首关键字，ColumnDef 取列名，表达式取其首 Token，BinaryExpr/UnaryExpr 取运算符位置。

```text
CreateTableStmt(table: str, columns: list[ColumnDef], line, column)
ColumnDef(name: str, col_type: str, line, column)
InsertStmt(table: str, columns: list[str], values: list[LiteralExpr], line, column)
SelectStmt(columns: list[str] | Literal["*"], table: str, where: Expr | None, line, column)
DeleteStmt(table: str, where: Expr | None, line, column)

BinaryExpr(op: str, left: Expr, right: Expr, line, column)
UnaryExpr(op: str, operand: Expr, line, column)
IdentifierExpr(name: str, line, column)
LiteralExpr(value: int | float | str | bool, lit_type: str, line, column)
```

- `col_type` 仅为 INT、VARCHAR；`lit_type` 为 INT、FLOAT、VARCHAR、BOOL，均大写。`op` 使用文法中的运算符，逻辑运算符大写。
- `to_dict()` 递归输出以上结构字段并增加 `"node": "节点类名"`。WHERE 缺省输出 JSON null；不输出运行期附加属性。
- `resolved_type/expr_type` 是语义阶段标注，不进入 AST 展示或表达式计划 JSON。编排在 Parser 成功后立即保存结构快照。
- 表名、SELECT/INSERT 列名目前为字符串，无单独位置字段；这些名字的存在性错误定位到语句首位置。WHERE 标识符、CREATE 列定义和 INSERT 值的错误使用对应节点位置，不反查源文本发明位置。

验收树：`a=1 OR b=2 AND c=3` → `OR(a=1, AND(b=2,c=3))`；`NOT a=1` → `NOT(a=1)`；`a+1>b*2` → `>((a+1),(b*2))`。

## 4. 值转换与 Catalog

### 4.1 字面量转换

Lexer 只产生原文，Parser 构造 LiteralExpr 时完成转换：

- INT 转 Python int。数字词素大于 2147483647，报 ParserError，原因包含 `integer literal out of range`；超长数字也应先做范围检查，不能泄漏 Python 转换异常。
- FLOAT 转 Python float；结果不是有限数时，报 ParserError，原因包含 `float literal out of range`。
- VARCHAR 去外层引号并将 `''` 转为 `'`；UTF-8 长度检查归语义阶段。
- TRUE/FALSE 转 Python bool。类型判断以 `lit_type` 为准，不能因 Python bool 是 int 子类而允许 BOOL 插入 INT 列。

### 4.2 Catalog 接口与后端

C 在 `sql_compiler/catalog.py` 定义 Catalog：

```python
class Catalog:
    def __init__(self, json_path: str | None = None, *,
                 storage: StorageEngine | None = None) -> None: ...
    def create_table(self, name: str, columns: list[ColumnDef]) -> None: ...
    def find_table(self, name: str) -> TableSchema | None: ...
    def find_column(self, table: str, column: str) -> ColumnDef | None: ...
    def get_type(self, table: str, column: str) -> str | None: ...
    def list_tables(self) -> list[str]: ...
    def snapshot(self) -> Catalog: ...
```

`TableSchema` 是内存字典 `{"name": str, "columns": list[ColumnDef]}`。JSON 序列化时 columns 使用 §3.2 的 ColumnDef 结构字典；不能直接序列化 dataclass 对象。

- 参数有 `json_path` 时使用 JSON 后端；有 `storage` 时使用系统目录后端；二者都没有时为内存后端，不能同时指定二者。
- 查询统一小写比较，保留首次注册的原文用于展示；`list_tables()` 按小写表名排序，不返回系统表。
- `snapshot()` 返回独立内存副本，无文件或存储引用；在副本中建表不影响原 Catalog。
- JSON 根格式为 `{"tables":[<TableSchema 的 JSON 表示>,...]}`，表按小写名称排序、列保持定义顺序。文件不存在或为空时视为空目录；首次成功建表时写入。非空但非法的 JSON/结构应报错，不能静默当空目录。
- `create_table()` 检查空列列表、名字/类型合法性、重复表/列、保留名。重复表/列抛 SemanticError；没有语句位置的直接 API 调用采用首个 ColumnDef 的位置，空列表采用 1:1。常规 SQL 的精准重复诊断由 analyze 提前产生。
- JSON 写入采用同目录临时文件写完后替换，成功后更新内存；系统目录提交顺序见 §9.5。文件、页及格式故障使用 ExecuteError。
- 仅支持单实例使用。JSON 与页存储是两个显式运行模式，不自动迁移已有数据；公开查询与建表方法不随后端变化。

### 4.3 目录提交时点

| 调用路径 | CREATE 行为 |
|---|---|
| `analyze(stmts, catalog)` | 只检查，不注册、不持久化。编排按单条语句调用。 |
| `compile_sql(text, catalog)` | 使用 snapshot；每条 CREATE 全部编译成功后注册到快照，供后续编译可见；真实目录不变。 |
| `run(text, catalog, None)` | 阶段一教学模式；每条 CREATE 全部编译成功后，由 run 调一次真实 Catalog.create_table，提交 JSON 或调用方指定的内存目录。 |
| `run(text, catalog, storage)` | 数据库模式；编译成功后执行，CreateTable 算子只调一次 Catalog.create_table，由其创建物理表和目录记录。 |

阶段一其他语句只编译，不读写用户记录；`exec_result=None`。阶段三不能在语义分析时提前注册，也不能在执行器再次单独分配首页。

## 5. 语义、类型与算术

### 5.1 语义检查

C 实现 `analyze(stmts: list[Stmt], catalog: Catalog) -> list[Stmt]`，原地标注并返回传入列表。单次调用中的语句都针对传入 Catalog 检查，不隐式应用 CREATE；需要顺序可见性的调用方必须按 §4.3 逐句编排。

| 检查 | 裁决 |
|---|---|
| 表存在 | SELECT、INSERT、DELETE 必须引用已有用户表 |
| 列存在 | 检查 SELECT 列表、INSERT 目标列、WHERE 标识符 |
| CREATE | 表名、列名不得重复；大小写不同仍视为重复 |
| 系统表 | 用户 SQL 不得创建或访问 `__catalog__`，语义阶段拒绝 |
| INSERT | 列数和值数相等；目标列无重复，且恰好覆盖全部表列；允许调换顺序 |
| 类型 | 每个值与对应目标列类型相同；不补 NULL、默认值或隐式转换 |
| VARCHAR | 每个字面量 UTF-8 字节数不超过 255，超出时报 `varchar literal exceeds 255 bytes` |

结构和存在性错误可以首错停止；通过这些检查后，INSERT 逐列类型不匹配与值超长诊断要收集完整，再抛一个 SemanticError。双错基准为 `INSERT INTO student(id,name) VALUES('Alice',1);`，其中 id INT、name VARCHAR，必须同时指出两个不匹配值。

名字绑定通过后在 IdentifierExpr 标注 `resolved_type`；每个表达式标注 `expr_type`。INSERT 完整行按 Catalog 列序由执行器重排，不能把 SQL 目标列顺序直接当物理列序。

### 5.2 类型表

规则唯一实现位置为 `sql_compiler/types.py`：

| 运算 | 允许输入 | 结果 |
|---|---|---|
| `+ - * /` | INT、INT | INT |
| `= != > >= < <=` | INT、INT 或 VARCHAR、VARCHAR | BOOL |
| AND、OR | BOOL、BOOL | BOOL |
| NOT | BOOL | BOOL |
| WHERE | BOOL | 作为筛选条件 |
| INSERT | INT→INT，VARCHAR→VARCHAR | 原类型 |

其他组合均为 SemanticError，包括 FLOAT/BOOL 的插入和 FLOAT 参与运算。VARCHAR 比较采用 Python 字符串的区分大小写字典序。表达式即使在短路分支中，也必须完成语义类型检查。

### 5.3 执行期算术

- INT 范围为 `[-2147483648, 2147483647]`，每一步算术结果都检查范围。
- `/` 向零截断，例如 `(0-3)/2` 为 -1。使用绝对值的整数除法再恢复符号，不经浮点中转。
- 除零原因固定为 `division by zero`；越界为 `integer arithmetic out of range`。实际求值时转换成 ExecuteError；未求值的分支不产生算术错误。
- types.py 提供共享的 `checked_int_arithmetic(op: str, left: int, right: int) -> int`，失败抛 errors.py 统一定义的内部 `IntegerArithmeticError(reason)`。优化器与执行器复用该规则，分别采取保留子树或转换 ExecuteError 的行为。
- AND、OR 从左到右短路；其他二元表达式先左后右。WHERE 为常量也在扫描到记录时求值，空表不会求值谓词。

## 6. 计划与优化

### 6.1 JSON 计划接口

D 在 `sql_compiler/planner.py` 实现 `plan(stmt: Stmt, catalog: Catalog) -> dict`，输入已通过语义分析的单条 AST。输出是可直接传给执行器的 JSON 兼容字典。

```json
{"op":"CreateTable","table":"student","columns":[{"name":"id","type":"INT","line":1,"column":22}]}
{"op":"Insert","table":"student","columns":["id"],"values":[1]}
{"op":"Project","columns":"*","child":{"op":"SeqScan","table":"student"}}
{"op":"Delete","table":"student","child":{"op":"SeqScan","table":"student"}}
```

Filter 格式为 `{"op":"Filter","predicate":<表达式结构字典>,"child":<子计划>}`。所有字段固定：

- 原始 SELECT 恒为 Project → 可选 Filter → SeqScan；DELETE 恒为 Delete → 可选 Filter → SeqScan。缺 WHERE 省略 Filter，`SELECT *` 的 columns 是字符串 `"*"`。
- Insert.columns 与 values 对应 SQL 中的目标顺序；values 是 Python 原生值的列表，不是 LiteralExpr 对象或字典。
- CreateTable.columns 中 `type` 对应 ColumnDef.col_type；其中 `line/column` 保留列定义位置，执行器可仅凭计划重建 ColumnDef，系统目录也能保存位置。
- 算子节点自身没有行列字段；嵌套列描述和表达式保留源位置。表达式完全复用 AST.to_dict 的结构字段，不含语义标注。
- 表、列名可以保留原文，所有查找按小写比较。查询表头采用 Catalog 保存的列名，按 SELECT 列表顺序输出；`*` 使用 Catalog 顺序。

### 6.2 优化器规则

`optimize(plan: dict) -> tuple[dict, list[str]]` 返回独立的优化计划和规则名列表，禁止修改传入计划。

1. 常量折叠：两侧都是字面量且计算成功时，折叠算术或比较。除零、溢出子树保持原样，不提前报编译错误。新 LiteralExpr 继承被替换节点的位置。
2. 布尔化简按下表执行；常量 NOT 和删除 Filter[TRUE] 也属于布尔化简。

| 原式 | 化简 | 条件 |
|---|---|---|
| x AND TRUE、TRUE AND x | x | 类型已通过 |
| x OR FALSE、FALSE OR x | x | 类型已通过 |
| FALSE AND x | FALSE | 原式短路，不求值 x |
| TRUE OR x | TRUE | 原式短路，不求值 x |
| x AND FALSE | FALSE | 折叠后的 x 内没有任何 `+ - * /` 节点 |
| x OR TRUE | TRUE | 同上 |
| NOT TRUE、NOT FALSE | FALSE、TRUE | 常量 |
| Filter[TRUE] | 子计划 | 不改变扫描内容 |

无法证明被删除子树不会产生算术错误时保留原式。Filter[FALSE] 保留，避免额外改变扫描行为。本版不加入投影裁剪、谓词下推或消除 Project[*]。

### 6.3 遍历与验收

表达式采用后序遍历，每个节点先常量折叠再布尔化简；计划树先子后父，循环至一轮无变化。仅真正改变结构时记录规则，名称固定为 `constant_folding`、`boolean_simplification`，按首次触发顺序去重。

`Filter[1=1 AND age>10+8]` 应得到 `Filter[age>18]`，返回两种规则。验收同时断言规则名、结构和优化前后的结果或执行错误等价，不能只比较打印字符串。

## 7. 公共异常与结果

### 7.1 异常身份及边界

D 在 `sql_compiler/errors.py` 唯一定义异常；其他模块必须导入这些类，禁止复制同名基类。异常类型如下：

```text
CompileError(stage, line, column, reason)
├─ LexerError(line, column, reason, *, errors=None, tokens=None)
├─ ParserError(line, column, reason)
├─ SemanticError(line, column, reason, *, errors=None)
└─ PlannerError(line, column, reason)

ExecuteError(message)
IntegerArithmeticError(reason)  # 内部算术异常，不直接交给 CLI
```

各编译子类固定 stage 为 LEXER、PARSER、SEMANTIC、PLANNER。LexerError 和 SemanticError 的聚合列表仅含单条同类诊断，聚合位置取第一条；单条的 errors 为空列表。共享模块用延迟类型注解引用 Token，避免 errors 导入 lexer 形成循环。

底层编译函数失败必须抛异常；存储和执行故障抛 ExecuteError。只有 `run()` 在 SQL 提交边界将异常转换为 StmtResult；`compile_sql()` 保持异常式接口。模块不自行 print，也不能通过捕获所有异常隐藏编程缺陷。非法 SQL 产生 Python 原生回溯视为实现缺陷。

### 7.2 格式化错误

```text
[STAGE] Error at line X, column Y: reason
[EXECUTE] Error: reason
```

`run()` 按源码位置稳定展开同条语句的全部编译诊断，以 `\n` 连接，无末尾换行；原因中的控制字符转义为可见形式，不能混入真实换行破坏一条诊断一行的约定。CLI 直接逐行展示，不解析错误字符串来判断已完成阶段。

语义原因必须指出名字和类型，例如：

```text
column 'score' does not exist in table 'student'
operator '+' cannot be applied to INT and VARCHAR
column 'id' expects INT, but VARCHAR found
```

### 7.3 结果类型

D 在 `utils/results.py` 定义：

```python
@dataclass
class ExecuteResult:
    rows: list[tuple]
    message: str
    columns: list[str]

@dataclass
class StmtResult:
    ok: bool
    error: str | None
    tokens: list[dict] | None
    ast: list[dict] | None
    plans: list[dict]
    exec_result: ExecuteResult | None
    semantic_ok: bool | None
```

每个结果对应一个 §2.3 分段；成功时它包含一条语句。ast 为单元素结构字典列表；tokens 为供该段 Parser 使用的 Token 字典列表，type 序列化为枚举名称。plans 顺序固定为原始计划、优化计划。

| 结局 | tokens | ast | semantic_ok | plans | exec_result |
|---|---|---|---|---|---|
| 词法失败 | None | None | None | [] | None |
| 语法失败 | 有 | None | None | [] | None |
| 语义失败 | 有 | 有 | False | [] | None |
| 计划构造失败 | 有 | 有 | True | [] | None |
| 优化失败 | 有 | 有 | True | [原始] | None |
| 执行或阶段一目录提交失败 | 有 | 有 | True | [原始, 优化] | None |
| 阶段一成功、纯编译成功 | 有 | 有 | True | [原始, 优化] | None |
| 数据库执行成功 | 有 | 有 | True | [原始, 优化] | ExecuteResult |

成功时 `ok=True,error=None`；失败时 `ok=False` 且 error 非空。ok 表示所选模式要求的全部阶段成功，不以 exec_result 是否为 None 判断。优化期的潜在算术错误不属于“优化失败”，仍按 §6.2 保留原表达式。

## 8. 工程、命令行与测试

### 8.1 技术基线与模块归属

Python 3.11+，仅标准库及 pytest；requirements.txt 只有 pytest。不引入第三方解析/编译库。函数采用 snake_case，每个公共函数有中文 docstring。

工程目录为 `database_system/`，运行命令以该目录为工作目录；基础业务已经集成，v1.6 在此基础上扩展。具体进度见 [工程说明](../../database_system/README.md) 和 [扩展验证记录](../../database_system/docs/extensions_validation.md)。

```text
database_system/
  sql_compiler/
    __init__.py
    lexer.py parser.py ast_nodes.py semantic.py types.py
    catalog.py planner.py optimizer.py errors.py
  storage/
    __init__.py
    record.py storage_engine.py page.py buffer.py file_manager.py
  engine/
    __init__.py
    executor.py evaluator.py runtime.py
  cli/
    __init__.py main.py
  utils/
    __init__.py results.py
  tools/
    __init__.py case_runner.py
  tests/
    cases/compiler/
    cases/execution/
  requirements.txt
  grammar.md
  README.md
```

A 定义 Token 并负责 CLI、runner、端到端测试；B 定义 AST/ColumnDef、记录及表级存储；C 负责语义/types/Catalog/物理页缓存；D 负责计划优化、执行、公共异常结果、顶层编排与 CI。

允许导入冻结的公共类型及接口，通过依赖注入使用后端；不依赖别人的私有实现。底层类型模块不反向导入 runtime；`sql_compiler/__init__.py` 暴露 compile_sql 时采用调用时导入，避免 AST、Catalog、StorageEngine 之间因包初始化形成循环。M1 先建立全部接口存根和假数据测试，再开始各模块真实现。

### 8.2 命令行输出契约

```text
python -m cli.main [文件.sql] --mode compiler|database --data-dir 路径
```

- `--mode` 缺省为 database；compiler 使用 JSON 目录，database 使用页存储。阶段一必须显式选择 compiler，不根据 NotImplementedError 自动猜测模式。
- `--data-dir` 缺省为当前工作目录的 data，解析成绝对路径；正式结果不打印此路径。
- 文件或 stdin 读取完整 UTF-8 文本直到 EOF，允许起始 BOM。通过 tokenize 统一换行；无参数时不进入 REPL、不输出提示符。
- `--tokens --ast --plan --opt-plan` 四项默认开启；同时提供 `--no-tokens --no-ast --no-plan --no-opt-plan`，重复指定最后一个生效。
- 每条语句展示已完成阶段，随后展示该条错误，继续展示下一条。全部结果处理完才决定退出码；空输入无输出且退出 0。

每项一行，顺序及前缀如下：

```text
TOKENS: <Token JSON 数组>
AST: <AST JSON 数组>
SEMANTIC: OK
PLAN: <原始计划 JSON>
OPT_PLAN: <优化计划 JSON>
COLUMNS: <列名 JSON 数组>
ROW: <单行数据 JSON 数组>
RESULT: <执行消息>
<错误行>
```

四个开关只影响其对应项；semantic_ok 为 True 才打印 SEMANTIC 行。成功查询打印一条 COLUMNS、每条记录一条 ROW、最后一条 RESULT；零行查询仍有 COLUMNS 和 RESULT。非查询成功只追加 RESULT。compiler 模式不打印执行结果。失败不打印成功的执行结果，但保留此前阶段输出。

JSON 统一使用 `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)`；每个前缀冒号后恰好一个空格。stdout 编码 UTF-8、换行 LF，无额外空行、耗时或 Python repr；tuple 输出 JSON 数组。执行消息按 §9.4 固定。

全 SQL 成功为退出码 0，任一 SQL 失败为 1。参数、输入文件读取、数据库打开或关闭故障写 stderr 并退出 2，关闭故障优先于 SQL 退出码。CLI 在 finally 调用关闭接口。缓存日志、统计及调试走 stderr。

### 8.3 用例执行器与隔离

A 实现 `python -m tools.case_runner --suite compiler|execution|all`，默认 all，按路径排序运行。compiler 套件给 CLI 传 `--mode compiler`；execution 传 `--mode database`。阶段一明确只运行 compiler，不能自动跳过未就绪模块后宣称全绿。

用例为 `tests/cases/<套件>/<名称>.sql` 与同名 `.expected`：

- SQL 文件可含多条语句。每个用例分配独立临时目录并显式传 --data-dir，不碰用户默认目录。
- expected 最后一个非空行为 `EXIT: N`，此前为完整 stdout；EXIT 行由 runner 解释，不由 CLI 打印。
- 比较仅将 CRLF/CR 统一 LF、忽略末尾多余 LF，其他字符、空格逐字节一致；stderr 不参与 stdout 基准。
- 持久化重启测试由 pytest 在同一临时目录启动两个进程；该目录不跨用例共享。
- API 测试断言异常类型、位置、结果结构和磁盘行为，端到端测试补充精确输出。固定的英文协议文本必须原样输出，代码注释和解释使用中文。

用例库总计至少 60 条，每人至少 15 条，每人及总库错误用例均不少于一半；第一阶段验收时 compiler 套件满足该门槛，后续追加执行用例。同一用例只能计入一人的数量，不重复计数。不得用端到端通过替代模块单测。

### 8.4 必需验收场景

以下是待实现的验收要求，不表示当前已有测试通过：

| 类别 | 必测场景 |
|---|---|
| 四条基准 | AND/OR 优先级、未闭合字符串、缺分号、INSERT 双类型错误 |
| 恢复 | 一段内多词法错误、错误后有效语句、只有错误无 Token、字符串/注释内分号、尾部缺分号、空输入、单独分号、全局/局部 EOF 坐标 |
| 文法语义 | NOT、NULL、expect FROM/分号与 FIRST 的区别、保留字、重复/缺失 INSERT 列及列重排、255/256 字节字符串、INT/FLOAT 字面量边界 |
| 错误输出 | 各阶段截断、多错误顺序、阶段一提交失败、执行失败保留计划且继续后续语句、公共异常可被 run 捕获 |
| Catalog | compile_sql 快照不写真实目录；阶段一 CREATE 后 SELECT 可见及 JSON 重启；失败 CREATE 不注册；阶段三建表只登记一次 |
| 存储 | 三个 64 字符列名可持久化；59/60 张用户表边界；4076/4077 字节记录边界；页 0 和空闲链回收 |
| 删除和缓存 | 首槽删除后后槽仍可见、重复行按 RID 定位、删除后重启、跨页插入、容量 1 的页切换、LRU/FIFO 淘汰脏页、直接写盘后的缓存失效 |
| 求值优化 | 负数向零除法、除零、每步溢出、空表常量谓词、左右短路、可能报错的 x AND FALSE/x OR TRUE、优化前后结果或错误相同且原计划不变 |
| 命令行 | 四个关闭开关、文件/stdin/BOM/换行等价、空结果表头、精确 JSON、模式隔离、退出码及 stderr 分离 |

### 8.5 协作纪律

main 分支保护，模块开发使用各自分支；普通代码 PR 至少一人审阅。commit 使用 `[lexer]`、`[parser]` 等模块前缀。契约变更通过 PR 且四人全部批准，版本递增。任何人不得为迁就自己的代码擅改别人的模块；接口偏差由责任方修正。可以提出契约缺口，未经批准不能改变已有接口。

## 9. 存储、执行与顶层编排

### 9.1 类型物理格式与记录

B 在 `storage/record.py` 实现：

```python
def serialize(record: tuple, columns: list[ColumnDef]) -> bytes: ...
def deserialize(data: bytes, columns: list[ColumnDef]) -> tuple: ...
```

所有多字节整数小端。INT 为 4 字节有符号数；VARCHAR 为 2 字节无符号字节长度及 UTF-8 数据，最长 255 字节。记录为 1 字节保留头（固定 0）加各列值，顺序等于 Catalog.columns。

serialize 检查完整列数、实际值类型、INT 范围和字符串字节数；与物理格式不符时抛 ExecuteError。deserialize 检查长度、保留头、UTF-8 和完整消费，不把损坏数据变成 Python 原生异常。墓碑判断归扫描槽目录，deserialize 不看删除标志。

本版不支持跨页记录。插入前计算完整记录长度，超过 **4076 字节**时报 `record exceeds 4076 bytes`，在任何页分配或修改前拒绝。

### 9.2 数据页、槽目录与元数据页

页固定 4096 字节，页号从 0 开始。页 0 专用，数据页号大于 0。空链指针 `0xFFFFFFFF` 与删除槽偏移 `0xFFFF` 是不同字段的哨兵。

数据页头为 `<IHHII>`，共 16 字节：

| 偏移 | 字节数 | 字段 |
|---|---|---|
| 0 | 4 | page_id |
| 4 | 2 | num_records：已分配槽数，含墓碑槽 |
| 6 | 2 | free_space_offset：记录末端偏移，初始 16 |
| 8 | 4 | prev_page_id |
| 12 | 4 | next_page_id |

记录从页头之后向后追加，槽目录从页尾向前生长。第 i 个槽（从 0 开始）位于 `4096 - 4*(i+1)`，格式 `<HH>`，分别为 record_offset、record_len。

- 可插入条件：`free_space_offset + record_len <= 4096 - 4*(num_records+1)`。
- 删除仅将槽 offset 改为 `0xFFFF`，不改变槽长度、槽数或数据末端，不压缩、不复用墓碑槽。
- 单页单记录上限为 `4096-16-4=4076`。完整记录为 4076 时允许，4077 时拒绝。
- 表内数据页为双向链，只有首/尾的外侧指针为 `0xFFFFFFFF`。

页 0 头为 `<4sHHII>`，共 16 字节：

| 偏移 | 字节数 | 字段 |
|---|---|---|
| 0 | 4 | 魔数 MSQL |
| 4 | 2 | 存储格式版本 1 |
| 6 | 2 | 表映射条目数量 |
| 8 | 4 | 空闲页链首 |
| 12 | 4 | 保留 0 |

余下 4080 字节为 **60 个 `<64sI>` 条目**：小写 ASCII 表名占 64 字节，不足补零，恰好 64 字节无需终止符；后接 4 字节首页号。有效条目连续保存，未使用条目全零。含系统目录在内最多 60 张表，即 **59 张用户表**；满时在分配或修改前报 `table limit exceeded (59 user tables)`。

B 管表数、映射和数据页链；C 管空闲链首。更新页 0 时读取同一缓存中的最新页，只改自己负责的字节，不能回写旧的整页快照覆盖另一方字段。

### 9.3 页存储与缓存

C 的 `storage/file_manager.py` 对外提供 PageStore，构造参数为 `PageStore(path: str, capacity: int = 64, policy: str = "LRU")`；Page 在 page.py，BufferPool 在 buffer.py。公开接口为：

```python
class PageStore:
    def read_page(self, page_id: int) -> bytes: ...
    def write_page(self, page_id: int, data: bytes) -> None: ...
    def get_page(self, page_id: int) -> Page: ...
    def flush_page(self, page_id: int) -> None: ...
    def alloc_page(self) -> int: ...
    def free_page(self, page_id: int) -> None: ...
    def stats(self) -> dict: ...
    def flush_all(self) -> None: ...
    def close(self) -> None: ...
```

- Page 为 `{id: int, data: bytearray, dirty: bool}`，data 长度恒 4096。capacity 至少 1；policy 为 LRU 或 FIFO，默认 LRU。BufferPool 的容量及策略由 PageStore 传入。
- get_page 走缓存；命中/未命中计数分别递增。LRU 命中更新最近使用顺序，FIFO 命中不改变入队顺序。淘汰前写回脏页，成功后 evictions 加一，并向 stderr 记录 `[BUFFER] evict page=<id>`。
- stats 返回 `{"hits": n, "misses": n, "evictions": n}`，自身不打印。命中统计以实际 get_page 调用为准，包括存储内部调用；直接读写和刷盘本身不增加命中统计。
- 修改 get_page 返回的页后，必须在下一次可能访问其他页的存储调用前设 dirty=True。Page 引用不能跨可能淘汰它的调用继续用于修改；再次修改必须重新 get_page。遍历或 yield 前提取不可变记录/下一页号，不能在缓存容量 1 时依赖旧 Page 仍在池中。
- read_page 绕过缓存返回磁盘快照，不要求反映未刷脏页。write_page 是调试用整页覆盖，必须正好 4096 字节；成功后丢弃该页旧缓存（不得随后把旧脏页写回覆盖它）。调用方此前持有的 Page 引用失效。
- flush_page 写回并清除对应脏标志；不在缓存中则无需操作。flush_all 写回所有脏页；close 先 flush_all 再关闭文件，可重复调用。成功写回不改变缓存替换顺序。
- 文件不存在或长度为 0 时初始化页 0，格式号为 1、表数 0、空闲链首为 `0xFFFFFFFF`。已存在非空文件必须页对齐并校验魔数/版本；坏文件报 ExecuteError，不能自动清空。
- alloc_page 优先取空闲链首，否则追加一页；返回页先清零，B 随后初始化数据页头。空闲页前四字节是下一空闲页号，其余清零。
- free_page 仅接受已脱离所有表链的页，禁止页 0、无效页和重复释放。由 B 保证先解除表链引用；C 验证页号及空闲链状态，不解析用户表结构。普通 INSERT/DELETE 不回收表页。
- 读写无效页、短读、I/O 故障均转 ExecuteError。底层不捕获后继续伪造成功；flush_all 和 close 的失败必须传给调用方。

### 9.4 表级接口与执行结果

B 在 `storage/storage_engine.py` 实现 `StorageEngine(pages: PageStore)`，依赖注入 C 的页后端：

```python
RecordId = tuple[int, int]  # 页号、槽号

class StorageEngine:
    def has_table(self, table: str) -> bool: ...
    def create_table(self, table: str) -> None: ...
    def insert_record(self, table: str, row: tuple,
                      columns: list[ColumnDef]) -> RecordId: ...
    def scan_records(self, table: str, columns: list[ColumnDef]
                     ) -> Iterator[tuple[RecordId, tuple]]: ...
    def delete_record(self, table: str, rid: RecordId) -> None: ...
    def update_record(self, table: str, rid: RecordId, row: tuple,
                      columns: list[ColumnDef]) -> RecordId: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

- 表名小写归一化；has_table 只查物理映射。create_table 检查映射容量、分配并初始化首页、登记页 0；它不读写 Catalog。
- insert_record 先校验并序列化，再向表尾页追加，空间不足则分配并连接新尾页。不复用删除槽。
- scan_records 按表链顺序、槽号递增，跳过墓碑，返回 `(RecordId, tuple)`。RID 是内部定位信息，不进入最终查询行。
- delete_record 验证 RID 属于目标表且槽有效，然后标记墓碑；重复删除已删除槽无额外作用。无效 RID 抛 ExecuteError。
- update_record 先校验编码和 RID；长度不增长时原槽覆盖并更新长度，否则先插入新行再删除旧槽，返回新 RID。已删除槽不能更新。调用方必须先计算并预检完整更新目标集，不能一边扫描一边迁移更新。
- 页链越界、循环、页头与实际页号不符或损坏槽均报 ExecuteError。数据页解析由 B 负责，C 仅提供字节和缓存。
- flush 委托 pages.flush_all；close 委托 pages.close，可重复调用。D 不绕过 StorageEngine 修改页头、槽和表映射。

D 实现 `execute(plan: dict, catalog: Catalog, storage: StorageEngine) -> ExecuteResult`，查询 rows 为元组列表、columns 为表头；非查询 rows、columns 都为空。消息固定为：

| 算子 | message |
|---|---|
| CreateTable | `OK` |
| Insert | `1 row inserted` |
| SELECT | `N row selected`（N=1），其他为 `N rows selected` |
| Delete | `N row deleted`（N=1），其他为 `N rows deleted` |
| Update | `N row updated`（N=1），其他为 `N rows updated`；N 为匹配行数 |

### 9.4.1 顶层编排及生命周期

D 在 `engine/runtime.py` 实现：

```python
def open_database(data_dir: str, *, mode: str = "database"
                  ) -> tuple[Catalog, StorageEngine | None]: ...

def run(text: str, catalog: Catalog,
        storage: StorageEngine | None = None) -> list[StmtResult]: ...

def close_database(catalog: Catalog,
                   storage: StorageEngine | None) -> None: ...
```

- open_database 只接受 compiler、database。compiler 打开 `<data_dir>/catalog.json` 并返回 storage=None；database 打开 `<data_dir>/minisql.db`，依次创建 PageStore、StorageEngine、Catalog 后返回。两种模式不隐式读取或迁移另一后端。
- run 是 CLI 唯一 SQL 提交入口；CLI 可以调用打开和关闭接口。storage=None 明确表示阶段一模式，不通过捕获存根异常猜测模式。数据库模式的 Catalog 必须绑定传入的同一个 storage。
- run 扫描一次，按 §2.3 分段；逐段 parse → analyze → plan → optimize → 阶段一目录提交或 execute。存下每一阶段结构快照，按 §7.3 生成结果。错误语句之后继续下一段，函数不打印。
- close_database 在 compiler 模式无额外目录提交；在 database 模式调用 storage.close。初始化中途失败时，runtime 关闭已打开资源，保留原始故障原因。
- I/O 失败不承诺事务回滚；仍以错误结果返回，后续语句按当前可用状态处理。验收保证正常完成和正常关闭后的持久化，不引入 WAL、并发事务或崩溃恢复。

D 在 `sql_compiler/__init__.py` 提供 `compile_sql(text: str, catalog: Catalog) -> list[StmtResult]`。它和 run 复用内部 Token 编译辅助逻辑，使用 Catalog.snapshot，逐段编译、注册成功 CREATE 到快照；首个失败段抛原始 CompileError（该段的聚合诊断保留），不返回失败结果、不执行、不写真实目录。成功列表的 exec_result 全为 None。

compile_sql 先完成扫描和分段，再按源码顺序确定首个失败段。全局 LexerError 不能直接重新抛出而覆盖更早分段的语法或语义错误；轮到词法失败段时，仅聚合该段的诊断，异常携带该段的有效 Token 和局部 EOF。

### 9.5 系统目录及启动

C 的 Catalog 在页后端使用每列一条记录，内部固定 Schema 为：

```text
__catalog__(
    table_name VARCHAR,
    column_name VARCHAR,
    column_type VARCHAR,
    ordinal INT,
    source_line INT,
    source_column INT
)
```

- 表、列名保存首次注册的原文；column_type 为 INT 或 VARCHAR；ordinal 从 0 开始，source_line/source_column 保存 ColumnDef 位置。
- 固定内部 Schema 在 catalog.py 中直接构造，内部列定义使用 1:1 位置，不经 SQL、不查询 Catalog。
- 系统目录自己的 Schema 不写入系统目录记录。用户查询接口不暴露它；只有 Catalog 通过 StorageEngine 使用此表。
- 新数据库的判定为文件首次创建或原本长度为 0，由 runtime 在打开 PageStore 前确定。新库初始化页 0 后，runtime 通过 StorageEngine.create_table 创建 `__catalog__` 并 flush，再构造页后端 Catalog。
- 已有非空数据库打开时，必须存在系统目录；缺失、目录结构损坏、列序号不连续或目录引用不存在的物理表均报损坏，不能悄悄新建系统表。Catalog 通过 has_table 校验目录中每张用户表的物理入口。
- Catalog 用固定内部 Schema 调 scan_records，按表名分组并按 ordinal 排序重建用户 TableSchema；系统目录无需再查自身，因此不会循环引导。
- Catalog.create_table 先校验逻辑结构，并由 StorageEngine 在写前检查物理映射容量；随后建物理表、插入该表各列的目录记录、flush，成功后更新内存 Catalog。
- 执行器从计划的列描述重建 ColumnDef，交给 Catalog；它不另行 alloc_page。每列一条目录记录可跨多个目录数据页，不受单个 255 字节 JSON 字段限制。

### 9.6 表达式求值

D 在 `engine/evaluator.py` 实现 `evaluate(expr: dict, row: tuple, columns: list[ColumnDef]) -> object`：

- IdentifierExpr 按小写列名在 columns 中找到下标，再取 row 值；LiteralExpr 返回 value。
- BinaryExpr/UnaryExpr 按 §5 类型、算术及短路规则求值，共享 checked_int_arithmetic。内部算术异常转 ExecuteError。
- 函数不修改表达式、行或 Schema，不访问 Catalog 或存储。M1 就建立该接口存根。

### 9.7 算子执行约定

| 算子 | 读写方式 |
|---|---|
| CreateTable | 从计划构造 ColumnDef，只调用 Catalog.create_table 一次 |
| Insert | 按 Catalog 列序重排目标值，调用 StorageEngine.insert_record |
| SeqScan | 调 scan_records，内部保留 RID 和完整行 |
| Filter | 对行求值，保留满足条件的 RID 和行 |
| Project | 按 SELECT 列序投影，丢弃内部 RID，输出 tuple |
| Delete | 先完整计算目标 RID 列表，筛选成功后再逐一 delete_record |

删除先筛选后写入，保证谓词求值错误发生时尚未标记任何记录；不承诺 I/O 中断时回滚已标记记录。没有 WHERE 的 DELETE 扫描所有活记录。执行正常收尾统一 storage.flush，刷盘成功后才能返回成功结果；其余剩余脏页由关闭接口写回。

## 10. 定稿范围与后续交付

v1.6 保持基础接口和磁盘格式，并增加 §11 扩展。基础阶段的分工以 [team_plan.md](team_plan.md) 为准；交接摘要不能覆盖或重定义契约。

M1 的导入、签名、异常身份与结构快照检查保留；基础数据库已经完成集成回归，扩展以真实模块与独立数据库进行验收。实践报告、UI 分支合并和现场答辩按后续交付安排完成。

最终交付包括模块化源码、README、运行说明、与 §2.1 一致的 grammar.md、AST/Catalog/Plan 及物理格式说明、测试结果与失败案例分析、实践报告和 AI 辅助使用说明。每位成员必须能说明自己模块的数据流、函数职责和现场修改方法。

## 11. v1.6 独立扩展

本节是基础条款的增量规范，适用于用户已授权独立完成的 UPDATE、ORDER BY、INNER JOIN、GROUP BY 和 HAVING。详细示例、数据流和 UI 交接见 [扩展说明](../../database_system/docs/extensions.md)。

### 11.1 查询作用域与结构

- SelectStmt 保留原字段并增加可选 items、alias、joins、order_by、group_by、having。SelectItem 保存表达式或星号及结果别名；JoinClause 保存右表、别名及 ON；OrderItem 保存表达式和方向。IdentifierExpr 可带 qualifier，AggregateExpr 保存函数名和列参数，COUNT(*) 的参数为空。
- UPDATE 使用 UpdateStmt(table,assignments,where)，Assignment 保存目标列和右侧表达式。所有新语法节点保留行列。
- 未使用的扩展字段不进入旧 AST 输出；语义绑定、位置索引和类型标注不进入 AST 快照。基础 SELECT 继续使用原始 AST／计划形式。
- 查询作用域按 FROM 和 JOIN 顺序建立。表别名替代原表名作为限定名，禁止重复限定名；未限定列必须唯一。每个 ON 只能引用当时已经引入的表。系统目录不能作为任意查询来源。
- 扩展计划的 BoundColumnExpr 保存 index、value_type、line、column，按输入行内位置求值，不再根据结果表头查找。新 SelectItem 的名称错误使用对应节点位置，基础字符串列列表维持原定位方式。

### 11.2 关系算子

- 扩展 SeqScan 增加 output 列描述；NestedLoopJoin 使用 left／right 和 predicate，嵌套循环按书写顺序执行，不去重、不重排连接。
- Filter 保留原形式。Aggregate 保存 keys、aggregates、child；输出位置为分组键在前、聚合项在后。Sort 保存 keys(expr,descending)、child；最终 Project 的 items 保存 expr、label。旧 Project(columns,child) 继续支持。
- 顺序为扫描／连接、WHERE、聚合、HAVING、排序、最终投影。内部结果同时保存列结构及行集合，空结果仍有完整表头。
- ORDER BY 支持多个键，默认 ASC，同键保持输入顺序。排序键只允许列、限定列、结果别名及聚合项；非分组查询允许未投影来源列作为排序键。字符串按 Unicode 码点；空值升序在前，降序在后。
- SELECT 星号按来源顺序展开，限定星号仅展开该来源。连接查询未指定结果别名时表头使用限定名称，普通单表结果保持原列名。
- 优化器访问 child／left／right，不跨连接、聚合或排序移动条件；常量折叠与布尔化简必须保持可观察结果及执行错误等价。

### 11.3 更新

- 支持单表、多列赋值与可选 WHERE；目标列存在且唯一，所有右侧表达式读取更新前原行，类型必须与磁盘列精确匹配。
- 执行器先收集目标、求值并序列化预检全部新行，再逐一 update_record，成功后 flush。表达式、类型、长度或记录容量错误发生时尚未修改任何目标。
- 匹配行都计数，包括值未改变的行；零匹配返回 0 rows updated。物理迁移可能改变无 ORDER BY 查询顺序。
- 继续采用正常完成和正常关闭的持久化保证，不增加 I/O 故障回滚、事务或文件格式迁移。

### 11.4 聚合及结果类型

- 支持多列 GROUP BY、COUNT(*)／COUNT(column)、SUM、AVG、MIN、MAX。后四者参数限列，SUM／AVG 仅接受 INT，MIN／MAX 接受 INT 或 VARCHAR；禁止聚合嵌套和 DISTINCT 参数。
- 无分组键的聚合针对全表；空输入返回一行，COUNT 为 0，其他聚合为 None。带分组键的空输入返回零行。重复聚合表达式共享计算结果。
- COUNT／SUM 使用有符号 64 位查询结果整数，AVG 使用有限浮点结果，MIN／MAX 保持列类型。AVG 中间和可用 Python 整数。聚合后的整数运算向零截断并检查 64 位范围，浮点运算结果必须有限。
- GROUP BY 或聚合存在时，SELECT／HAVING／ORDER BY 的所有非聚合列必须属于分组键；WHERE／ON／UPDATE 不允许聚合。HAVING 必须用于分组或聚合并产生 BOOL。
- HAVING／ORDER BY 可以引用结果别名；别名与来源列同名时必须报歧义，禁止静默优先绑定。结果别名不能重复，GROUP BY 不使用结果别名。
- 空值只在查询结果中产生。比较任一侧为空得到未知，NOT 未知仍为未知；未知 AND FALSE 为假、未知 OR TRUE 为真，其余按三值逻辑；条件只保留真值。算术任一侧为空返回空值。AND／OR 仍从左到右短路。
- ExecuteResult 的 rows／columns／message 和 StmtResult 的外层字段不变；JSON 输出宽整数、有限数值或 null。磁盘列仍只允许 INT／VARCHAR，普通整数表达式仍按 32 位规则执行。

### 11.5 UI 与验收

- open_database／run／close_database／compile_sql 及 UI 已使用的 _scan_and_segment／_compile_segment 保持签名和返回约定。纯编译及实时检查不执行 UPDATE。
- UI 由同伴在独立分支维护，最终合并后验证新算子树、结果别名、空值、实时错误定位和更新结果。不在数据库扩展中修改 UI 文件。
- 测试覆盖每项功能、组合查询、异常恢复、旧数据库重启与优化等价。SQL 用例配完整预期；演示脚本使用独立数据库且核对结果。实际执行证据见 [验证记录](../../database_system/docs/extensions_validation.md)。
