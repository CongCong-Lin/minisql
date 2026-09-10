# MiniSQL 文法（v1.6）

以下为基础功能与可选扩展的统一文法。花括号表示重复，方括号表示可选。

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

- 关键字不区分大小写。原有 23 个保留字保持不变；AS、INNER、ON、HAVING、ASC、DESC 和聚合函数名按上下文识别，仍可作为普通表列名。
- 隐式表别名避开子句起点及不支持的连接修饰词；这些名称作为别名时应显式使用 AS。
- 点号用于限定列；数值仍要求小数点两侧都有数字，不接受 `.5`、`1.`、指数表示或一元正负号。
- 聚合函数只有 COUNT 允许星号；SUM／AVG 只接受 INT 列。聚合出现在 WHERE、ON、UPDATE 或 DELETE 条件中时由语义阶段拒绝。
- 分组后的非聚合引用必须属于分组键。HAVING 只能用于分组或聚合查询。HAVING／ORDER BY 可以引用结果别名，但与来源列同名时报告歧义。
- UPDATE 支持单表和多列赋值，所有右侧表达式读取原行；WHERE 缺省时更新全表。
- ORDER BY 默认升序，支持列、限定列、结果别名和聚合项；不支持序号或任意算术排序表达式。
- 不支持外连接、USING、NATURAL JOIN、逗号连接、子查询、DISTINCT、DROP TABLE、NULL 输入、默认值、普通 SELECT 列表算术表达式或 VARCHAR 长度参数。

详细行为、计划结构和 UI 兼容说明见 [扩展说明](docs/extensions.md)。
