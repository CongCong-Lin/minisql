# MiniSQL 扩展实现与 UI 对接（v1.6）

在 `develop` 的基础实现上增加 UPDATE、ORDER BY、INNER JOIN、GROUP BY 和 HAVING。数据库扩展由当前负责人独立维护；同伴在 `feat/sqlyog-ui` 开发界面，最后合并。本次不修改界面源码。

## 使用与演示

在 `database_system` 目录执行：

```powershell
python -m tools.extension_demo
python -m tools.extension_demo --details
```

每次演示创建独立的 `data/extensions-时间-随机值/` 目录，保留数据库和 `execution.log`，不会复用现有演示库。八步分别验证建库、排序、更新、连接、聚合、错误恢复、空表聚合和重新启动读取。脚本逐步检查查询列、记录、错误阶段和退出码，符合预期才继续。

`examples/extensions/` 保存 SQL 与人工定义的查询预期，可作为答辩操作脚本。直接执行单个文件的方式不变：

```powershell
python -m cli.main examples/extensions/02_order.sql --mode database --data-dir data/你的演示目录
```

后续查询需要先在同一目录执行 `01_setup.sql`。重复演示请运行工具创建新库，不要重复建表。

## 语言行为

### 更新

```sql
UPDATE student SET score=score+5,name='张三' WHERE id=1;
UPDATE t SET a=b,b=a;
```

- 单表、多个赋值、可选 WHERE；缺省 WHERE 表示全表更新。
- 所有右侧表达式读取原行，第二例交换 a、b，不采用从左到右覆盖后的值。
- 目标列必须存在且不得重复，类型与磁盘列精确匹配；不允许聚合、子查询或多表更新。
- 先计算并编码预检所有目标行，再开始写入。除零、整数溢出、字符串或总记录超限不会造成前面记录已被修改。
- 返回匹配行数，值未改变的匹配行也计数；消息为 `1 row updated` 或 `N rows updated`。
- 物理更新短记录原位覆盖；变长时先插入，再删除旧槽。目标记录列表先固定，不会重复更新新插入的记录。
- 不承诺 I/O 失败时回滚已写入内容，不提供事务或崩溃恢复。

### 排序与列别名

```sql
SELECT name AS who FROM student ORDER BY score DESC,id ASC;
SELECT name AS who FROM student ORDER BY who;
```

- 支持多个键及分别指定 ASC／DESC，默认 ASC；同键保持输入顺序。
- 键可以是列、限定列、结果别名或聚合项，不支持序号与任意算术排序表达式。
- 非分组查询可以按未投影的来源列排序；分组查询的普通排序列必须是分组键。
- 结果别名与来源列同名时，在 HAVING／ORDER BY 中报告歧义。限定来源列可消除该歧义；建议为计算结果使用不同名称。
- 字符串按 Unicode 码点比较，不提供拼音或语言区域排序；空值升序在前、降序在后。
- 没有 ORDER BY 时不承诺结果顺序，尤其是变长 UPDATE 可能改变物理扫描位置。

### 内连接

```sql
SELECT s.name,c.title FROM student s INNER JOIN course AS c
ON s.id=c.student_id ORDER BY s.id,c.title;
```

- JOIN 等价于 INNER JOIN，可串联多表、自连接；每个 ON 只允许访问当前已经引入的表。
- 有表别名后，通过别名限定列。未限定列必须唯一，重名列报歧义。
- ON 支持比较、逻辑和整数算术，结果必须是 BOOL；保留一对多、多对多产生的重复记录。
- `*` 按来源表顺序及表内列序展开；`s.*` 仅展开 s。连接结果未显式指定别名的表头使用 `限定名.列名`，普通单表查询仍使用列名。
- 执行策略为从左到右的嵌套循环，适用于教学规模数据。没有连接重排、索引扫描、外连接、USING 或隐式逗号连接。

### 分组、聚合与 HAVING

```sql
SELECT team,COUNT(*) AS n,SUM(score) AS total,AVG(score) AS mean
FROM student GROUP BY team HAVING mean>80.5 ORDER BY total DESC;
```

- 多列 GROUP BY，五种聚合；没有 GROUP BY 的聚合查询作用于全表。
- COUNT 支持星号或列，其他函数只支持列。SUM／AVG 只接受 INT；MIN／MAX 接受 INT、VARCHAR。
- COUNT／SUM 返回受检的有符号 64 位整数，AVG 返回有限浮点数；MIN／MAX 保留输入类型。AVG 中间和使用 Python 整数，最终结果必须有限。
- 空输入的全表聚合返回一行：COUNT 为 0，其他结果为 `None`，经 JSON 输出为 `null`。带分组键的空输入返回零行。
- 普通选择列、HAVING 普通列和普通排序列必须出现在分组键中；支持仅分组、不使用聚合函数的查询。
- HAVING 可引用聚合及结果别名，必须产生 BOOL。与空值的比较产生未知值；NOT 未知仍为未知，未知 AND FALSE 为假、未知 OR TRUE 为真。只保留条件为真的组。
- 聚合后允许宽整数及浮点数参与数值比较、HAVING 中的算术；普通 WHERE／ON／UPDATE 保持原有整数算术规则。聚合后的整数除法同样向零截断。
- WHERE／ON／UPDATE 禁止聚合；不支持聚合嵌套、DISTINCT 参数、聚合表达式参数及普通 SELECT 列表算术表达式。
- 空值和小数仅为查询结果，不支持把它们作为新磁盘字段类型或通过 INSERT／UPDATE 存储。

## 内部结构与接口

AST 新增 SelectItem、OrderItem、JoinClause、AggregateExpr、Assignment、UpdateStmt。SelectStmt 保留旧构造参数，增加可选 items、alias、joins、order_by、group_by、having；IdentifierExpr 增加可选 qualifier。未使用的扩展字段不进入旧 AST 输出。

查询绑定由 `sql_compiler/query_binding.py` 负责，维护本次查询的来源列位置，不修改 Catalog。绑定信息不进入 AST 快照；在执行计划中使用 `BoundColumnExpr(index,value_type,line,column)` 明确引用当前输入行的位置。

| 计划节点 | 新字段及含义 |
|---|---|
| SeqScan | 扩展查询增加 output 列描述，保留 table |
| Project | 旧 columns 形式仍可执行；新 items 保存 expr 与 label |
| Sort | keys 保存 expr、descending，child 是被排序的行集合 |
| NestedLoopJoin | left、right 两个子计划，predicate 为 ON 条件 |
| Aggregate | keys 为分组键；aggregates 保存 function、argument、value_type |
| Update | table、assignments(index,expr)、child 保存筛选后的原记录 |
| Filter | 形式不变，WHERE 位于聚合前，HAVING 位于聚合后 |

优化器递归访问 child／left／right，只应用原有安全的常量折叠和布尔化简，不越过排序、连接、聚合边界移动条件。原始计划和优化计划保持独立。

内部关系执行器保存列描述及行集合，所以空结果仍有表头。查询列绑定依赖位置，不依赖展示名称是否重复。

新增存储接口：

```python
def update_record(self, table: str, rid: RecordId, row: tuple,
                  columns: list[ColumnDef]) -> RecordId: ...
```

返回更新后的物理记录位置；长记录可能迁移，调用方不能假定返回值等于原 rid。已删除记录、非法位置或不属于目标表的位置必须报错。页大小、槽布局、元数据与目录格式仍是 v1，已有数据库不需要迁移。

## 给 UI 开发者

- `open_database`、`run`、`close_database`、`compile_sql` 的签名不变。
- StmtResult／ExecuteResult 外层字段不变，查询仍使用 rows、columns、message。
- `_scan_and_segment` 与 `_compile_segment` 的签名和返回约定不变，现有实时检查可继续调用。
- 编译模式和实时检查只分析 UPDATE，不修改记录；真正更新需要 database 模式。
- 列名按返回的 columns 展示，不从 AST 或单个表推测；同名结果列必须按位置处理。
- 行值可能出现较大的整数、浮点数及 `None`；建议在界面把 `None` 显示为 NULL，不要显示成空字符串或 0。
- 计划树展示同时遍历 child／left／right，支持新算子和新投影 items；聚合结果索引对应分组键在前、聚合项在后的内部行。
- 中文语义错误及原有阶段错误格式保持一致，不应通过固定英文错误字符串判断阶段。
- [接口样例](extensions_ui_samples.json) 保存真实运行返回的语句结果，包含排序、更新、连接、聚合、空值和错误。

## 验证与已知边界

模块及真实链路测试位于 `tests/test_extensions.py`；命令行、演示和跨进程兼容测试位于 `tests/test_extension_cli.py`。基础 485 项测试继续参与回归，不删除基础功能断言。

新增公共用例为 compiler 的 4 对 x 系列文件、execution 的 12 对 x 系列文件。预期输出由真实 CLI 捕获，并先独立核对查询结果及退出码；完整标准输出随后由用例执行器比对。参数化单测数量不等同于 SQL 公共用例数量。

使用 `python -m tools.case_runner --suite all` 验证全部公共用例；使用 `python -m pytest` 验证模块和集成测试。最终运行记录见 [扩展验证记录](extensions_validation.md)。

本轮侧重正确性和可解释性：连接为嵌套循环，行集合、排序和分组在内存中处理，不适用于无界数据量。保持原有单进程访问、正常关闭持久化边界，不提供并发读写协调或 I/O 故障事务回滚。Python 3.11／3.13 的远程持续集成结果应以实际流水线为准。
