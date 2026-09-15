# 八项可选扩展：第二版数据库

本轮在 `codex/optional-extensions` 集成命令行与桌面界面。保留原有查询扩展，增加完整空值、FLOAT／BOOL／DATE、单列非唯一 B+ 树索引、统计与简单代价模型、EXPLAIN、事务、数据库级并发锁和账号角色权限。

## 启动与演示

在 `database_system` 目录执行：

```powershell
python -m pip install -r requirements.txt
python -m tools.optional_demo
python -m tools.studio
python -m cli.main examples/optional_extensions.sql --data-dir data/my-new-demo --no-tokens --no-ast
```

演示 SQL 每块附有注释，文件内使用固定表名，需选择全新目录。自动演示脚本每次创建独立目录，另外生成足够多的记录验证索引选择，检查权限与并发锁；不会打印随机演示密码。崩溃注入由自动测试执行。

界面增加用户名、隐藏密码输入、开始事务、只读事务、提交、回滚、事务状态、索引信息和计划成本；账号菜单支持初始化、创建账号、改密和删除账号。每个会话的数据库操作在固定工作线程串行执行，等待锁时主窗口仍可处理事件。事务跨多次点击执行保持，关闭窗口回滚未提交事务。

## 存储格式与迁移

新版运行入口只写第二版格式。旧库先关闭全部客户端，执行以下命令生成新目录，迁移不覆盖源文件：

```powershell
python -m tools.migrate data/old-db data/new-db
```

迁移读取旧表、删除槽与记录，复制到临时新库，重开核对结构、行数与逐行值，再发布目标目录；失败清理临时目录。源文件迁移前后校验摘要，源目录与目标目录不得相同或互相覆盖。旧列迁移后默认允许空值。编译模式目录 JSON 写入版本 2，可读取旧版无版本字段的目录；不支持的版本与非法 JSON 分别报错。

磁盘页维持 4096 字节。页零保存格式版本、数据库 UUID、系统目录根页、空闲页链和下一对象编号；目录本身是一张可扩展的系统堆表，分块保存表、索引、权限及统计元数据，不再限制六十个表。数据页保留十六字节页头，首四字节的高两位标识页类型、低三十位标识所属对象，后续为槽数、空闲偏移及双向页链。索引节点独占页，使用统一缓存、分配器与日志。

非空记录保留紧凑编码；含空值才加入位图。最大记录仍为 4076 字节。浮点保存有限双精度数，布尔值单独编码，日期保存相对纪元的天数。数据页直接按记录位置验证页归属和槽边界。堆表删除保留删除槽；当前不压缩堆表空洞，索引合并和目录重写释放的页可复用。

## 事务、日志与并发

默认一条语句一个自动提交事务，失败回滚该语句，后续独立语句继续。`BEGIN` 从开始获取独占锁，`BEGIN READ ONLY` 获取共享锁；禁止锁升级。显式事务中任何 SQL 失败进入失败状态，只能 `ROLLBACK` 或关闭连接。CLI 读完文件仍有未结束事务时回滚并返回失败。

写事务持久化日志头与原文件长度；每个旧页首次覆盖前写入旧页、事务编号和校验和并同步，缓存淘汰也遵守此顺序。提交先同步数据，再写入并同步提交标记。没有完整提交标记则恢复旧页并截断新增页；恢复可以重复执行。新事务清理旧日志后再开始，防止旧提交标记误用。提交标记同步失败返回 `COMMIT_UNKNOWN` 并关闭会话，重开恢复决定最终结果；提交已确认后清理日志失败不会撤销已提交数据。

`portalocker` 协调本机多进程及同进程独立句柄，每会话另有线程互斥锁。多个已连接的只读事务并行，写事务独占；新连接的初始化／恢复阶段使用独占锁。默认等待五秒，CLI 用 `--lock-timeout` 修改。Windows 需要 `portalocker[win32]`。新事务在锁内刷新缓存、目录与身份，长期会话能看到其他进程提交的内容和撤权。

## 类型与 SQL

支持 `INT`、`VARCHAR`、`FLOAT`、`BOOL`、`DATE`，列默认可空，`NOT NULL` 禁止插入或更新为空。保留显式 INSERT 列清单要求及原整数范围／整数除法。整数可提升为浮点；布尔值不作为整数，字符串不隐式转换为日期。

```sql
CREATE TABLE sample(id INT NOT NULL, score FLOAT, active BOOL, day DATE);
INSERT INTO sample(id,score,active,day) VALUES(1,NULL,TRUE,DATE '2024-02-29');
SELECT * FROM sample WHERE score IS NULL;
UPDATE sample SET score=98.5 WHERE id=1;
CREATE INDEX idx_sample_id ON sample(id);
ANALYZE sample;
EXPLAIN SELECT * FROM sample WHERE id=1;
EXPLAIN FORMAT JSON UPDATE sample SET score=100 WHERE id=1;
DROP INDEX idx_sample_id;
```

NULL 比较传播为未知，AND／OR／NOT 使用三值逻辑，WHERE 和连接只保留真值；聚合忽略空值（COUNT(*) 仍统计行），分组将空值归为一组，空值升序在前、降序在后。DATE 必须是真实日期；浮点拒绝无穷和非数值。

索引支持五种存储类型及空值，以键值和记录位置区分重复项，维护叶子链、分裂、借位、合并和根收缩。变长更新搬迁记录时同步全部索引的记录位置。索引创建、删除、数据修改和统计均参与同一事务。

代价模型只决定单表顺序扫描或索引扫描，不做连接重排。仅安全的列／常量比较、IS NULL 和 AND 可生成范围，最终仍复核完整条件；潜在算术错误和转换保守使用顺序扫描。顺序成本为 `页数 + 0.01 × 行数`；索引成本为 `树高 + 额外叶页数 + 4 × 预计回表页数 + 0.01 × 命中行数`。成本相同选顺序扫描。ANALYZE 统计空值数、不同值数和最小／最大值，写操作使分布统计过期；缺失或过期时采用教学默认选择率。小表或低选择性条件使用顺序扫描是预期行为。

EXPLAIN 支持 SELECT／INSERT／UPDATE／DELETE，做权限和语义检查，但不执行被解释语句。文本或 JSON 展示逻辑计划、执行计划及扫描节点的估计成本、行数和选择原因。纯 INSERT 无扫描选择，不虚构扫描统计；编译模式明确标记存储估算不可用。

## 账号与角色

默认新库免认证。显式初始化管理员后，该库所有正常客户端入口要求登录：

```powershell
python -m tools.admin init admin --data-dir data/new-db
python -m tools.admin create alice --user admin --data-dir data/new-db
python -m cli.main --data-dir data/new-db --user admin
```

密码由隐藏输入读取，不接受 SQL 或命令行密码参数。CLI 从标准输入读取 SQL 时，输入全部语句后用终端 EOF 结束；需要多次交互保留事务时使用桌面窗口或 Python 会话。

```sql
CREATE ROLE readers;
GRANT SELECT ON sample TO readers;
GRANT readers TO alice;
REVOKE SELECT ON sample FROM readers;
REVOKE readers FROM alice;
DROP ROLE readers;
CREATE ROLE builders;
GRANT CREATE ON DATABASE TO builders;
```

管理员管理账号、角色与授权。表所有者及管理员可管理索引和统计；角色可获得数据库级 CREATE 或表级 SELECT／INSERT／UPDATE／DELETE。连接涉及的全部表、EXPLAIN、表树及实时检查均检查权限；不支持角色嵌套。口令使用随机盐、PBKDF2-HMAC-SHA256 六十万次迭代和恒定时间比较，参数随记录保存，改密令旧会话身份失效。

## 公共接口与范围

`engine.session.connect(path, username=..., password=..., timeout=5)` 返回 `DatabaseSession`，提供 `run`、`begin(read_only=False)`、`commit`、`rollback`、`catalog_snapshot`、`inspect`、`close`。原 `open_database/run/close_database` 转入同一会话；原 v1 页接口只用于迁移和旧模块回归。逐语句结果增加 `error_code`，CLI 保留 SQL 失败退出码 1 与环境／恢复失败退出码 2。

该实现面向教学与本机磁盘。没有 MVCC、行级锁、唯一／复合索引、保存点、EXPLAIN ANALYZE、网络服务或文件加密。访问控制约束正常数据库客户端，不防御拥有操作系统文件写权限者，也不把内部存储对象视作安全沙盒。恢复测试覆盖进程强制退出与注入 I/O 故障，不表示已经验证所有硬件断电情形。

设计依据：[SQLite 原子提交顺序](https://www.sqlite.org/atomiccommit.html)、[portalocker 锁说明](https://portalocker.readthedocs.io/en/latest/quickstart.html)、[OWASP 口令存储参数](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#pbkdf2)。具体页和日志结构为本项目实现。
