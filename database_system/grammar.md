# MiniSQL 文法基线

来自 [公共契约 v1.5](../plan/handoff/contracts.md) 的 §2.1。M1 原样复制，Parser 完成时还须核对实现与文法一致。

```ebnf
program        -> statement_list EOF ;
statement_list -> { statement } ;
statement      -> create_stmt | insert_stmt | select_stmt | delete_stmt ;

create_stmt    -> CREATE TABLE IDENTIFIER '(' column_def { ',' column_def } ')' ';' ;
column_def     -> IDENTIFIER type ;
type           -> INT | VARCHAR ;

insert_stmt    -> INSERT INTO IDENTIFIER '(' id_list ')' VALUES '(' value_list ')' ';' ;
id_list        -> IDENTIFIER { ',' IDENTIFIER } ;
value_list     -> literal { ',' literal } ;
literal        -> INTEGER_CONST | FLOAT_CONST | STRING_CONST | TRUE | FALSE ;

select_stmt    -> SELECT select_list FROM IDENTIFIER where_opt ';' ;
select_list    -> '*' | IDENTIFIER { ',' IDENTIFIER } ;
where_opt      -> WHERE expression | ε ;

delete_stmt    -> DELETE FROM IDENTIFIER where_opt ';' ;

expression     -> or_expr ;
or_expr        -> and_expr { OR and_expr } ;
and_expr       -> not_expr { AND not_expr } ;
not_expr       -> NOT not_expr | comparison ;
comparison     -> arith_expr [ comp_op arith_expr ] ;
comp_op        -> '=' | '!=' | '>' | '>=' | '<' | '<=' ;
arith_expr     -> term { ('+' | '-') term } ;
term           -> factor { ('*' | '/') factor } ;
factor         -> IDENTIFIER | literal | '(' expression ')' ;
```
