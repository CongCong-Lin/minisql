# MiniSQL Grammar

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

Keywords are case insensitive. `NULL`, unsupported reserved statements, unary
signs, and SELECT list expressions are rejected by the parser.
