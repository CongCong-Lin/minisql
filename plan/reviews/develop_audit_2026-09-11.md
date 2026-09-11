# develop 功能与推送前审查

审查日期：2026-09-11。审查对象：本地 `develop@9b5ede0`。

修复跟进：用户随后要求修复两个 P2，修复已完成并通过 611 项回归；两个 P2 的最新状态见文末“两个 P2 修复验证”。下文保留原始审查事实和当时的推送判断。

## 结论

基础功能主体和本轮选择的 SQL 扩展已经实现，已有自动化测试全部通过。发现两项建议推送前修复的问题：深表达式导致多语句恢复失败，以及 Windows 标准输入编码导致中文被错误存储；另有一项损坏记录校验缺口及若干维护性改进。

代码具备作为开发版本上传 GitHub 的基础条件，但“能推送”“已有测试通过”和“完全满足课程验收”不是同一结论。本次没有修改实现、切换工作分支、创建提交或推送。

工作区仍位于 `feat/sqlyog-ui@6b7d67e`。审查时使用 `git archive develop` 导出的独立源码副本运行完整测试，没有把界面分支的 6 项测试或界面功能算入 develop。develop 本身不包含桌面界面。

## PPT 要求对照

材料为仓库 `ppt/` 下两份原始演示文稿，页码均按实际幻灯片顺序计数。

| 要求 | 来源 | develop 状态与说明 |
|---|---|---|
| CREATE TABLE、INSERT、SELECT、DELETE | SQL 编译器完整版第 6、41 页 | 已完成；INSERT 要显式写全字段列表，VARCHAR 不带长度参数 |
| Token 类别、行列位置、大小写、注释、字符串转义 | 编译器第 8–12 页 | 已完成；词法、语法、语义错误正常路径可定位 |
| 显式文法、结构化 AST、递归下降分析 | 编译器第 15–22 页 | 已完成；存在深层输入健壮性问题，见问题 1 |
| 表列存在性、名字绑定、类型和插入匹配 | 编译器第 24–28 页 | 已完成 |
| Logical Plan 与优化前后输出 | 编译器第 30–34 页 | 已完成；提供结构化 JSON |
| 至少两种优化、表达式与逻辑条件 | 编译器第 33、41 页 | 已有常量折叠、布尔化简，包含恒真 Filter 消除；没有 JOIN 谓词下推、扫描投影裁剪 |
| 页分配、释放、读写、行页映射 | 系统设计第 2、17 页 | 已完成；槽式记录、物理表映射和磁盘组织已实现 |
| LRU/FIFO、命中统计和日志 | 系统设计第 2、17 页 | 已完成；命中/未命中/淘汰统计，标准错误输出淘汰日志 |
| 系统目录按特殊表保存 | 系统设计第 2、14、17 页 | 数据库模式使用 `__catalog__`，关闭重开恢复；编译模式另用 JSON 目录 |
| UPDATE、ORDER BY、GROUP BY、JOIN | 编译器第 6、35、41 页 | 已完成单表多列更新、多键排序、多表内连接、自连接、分组及 HAVING |
| 聚合和算术表达式 | 编译器扩展范围 | 已实现 COUNT/SUM/AVG/MIN/MAX；普通整数算术和聚合后数值运算 |
| 错误恢复、序列化/JSON | 编译器第 35、41 页 | 已有按分号恢复和阶段 JSON；深层表达式可绕过恢复 |
| NULL、更多存储类型 | 编译器第 35 页，可选 | 未完整实现；空聚合可输出 null、AVG 输出浮点，但不能插入/更新为 NULL 或 FLOAT 字段 |
| 索引扫描、代价模型、EXPLAIN | 编译器第 35、41 页，可选 | 未实现；打印执行计划不等于实现 EXPLAIN 语句 |
| 事务、并发、访问控制、复杂索引 | 系统设计第 2、17 页，可选 | 未实现，文档已声明边界 |
| Plan 可视化、规则框架、Fuzz | 编译器第 35、38、41 页 | JSON 计划展示及固定规则优化已有；develop 无图形计划界面，未见正式持续随机测试体系；本次另做临时随机变异检查 |
| 模块源码、运行说明、设计、测试、报告、AI 使用说明 | 编译器第 42 页 | 源码、文法、契约、测试与演示已有；不应将其自动等同于完整课程实践报告、AI 使用说明或教师隐藏测试通过证明 |

PPT 明确写明高级扩展不要全部变成必做，因此没有索引、事务等不应单独判为基础功能未完成。

## 确证问题

### 1. P2：深层表达式中断整批 SQL，错误恢复失效

位置：`database_system/sql_compiler/parser.py:342`、`database_system/sql_compiler/ast_nodes.py:19`，以及 `database_system/engine/runtime.py:226–245` 的阶段异常边界。

在 Python 3.13.1 下，150 层括号触发解析递归上限，500 项 AND 条件触发 AST 递归序列化上限。`run()` 抛出未经转换的 `RecursionError`，没有返回带位置的失败结果，也不处理后续分号语句。

真实 CLI 顶层会捕获异常，退出码为 2，标准错误仅为 `maximum recursion depth exceeded`，标准输出为空。这不是所有入口都会直接退出进程，但 API 已中断，CLI 把 SQL 输入问题当作环境失败；此前成功的写入可能已经持久化，用户却看不到此前语句结果。

在工程目录运行的最小复现：

```python
from engine.runtime import run
from sql_compiler.catalog import Catalog

目录 = Catalog()
run("CREATE TABLE t(id INT);", 目录)
语句 = "SELECT id FROM t WHERE " + "(" * 150 + "id=1" + ")" * 150
run(语句 + "; SELECT id FROM t;", 目录)
```

另一复现把条件替换为 `" AND ".join(["id=1"] * 500)`。

建议：为表达式嵌套和结构深度设置明确、可测试的限制，或将递归处理改为显式栈；在单语句边界返回带源码位置的阶段错误，确保后续语句继续处理。不能只提升 Python 的递归上限或只修括号路径，因为 AST 序列化、深复制及优化也会受影响。增加合法/非法深层输入、长条件链、后续语句恢复和 CLI 退出码回归。

此问题与 PPT 第 5、11、38 页的健壮性要求有关，建议作为验收版本推送前优先修复项。

### 2. P2：Windows 管道输入可将中文错误存储，却返回成功

位置：`database_system/cli/main.py:47`、`:53–57`。

`_read_sql()` 用 `sys.stdin.read()` 读取标准输入，但初始化只为 stdout/stderr 指定 UTF-8，没有配置 stdin。Windows 默认 GBK 且未启用 Python UTF-8 模式时，UTF-8 管道字节被错误解码。

在 develop 独立副本中，通过真实子进程测试：移除子进程的 `PYTHONIOENCODING` 和 `PYTHONUTF8`，使用 `python -X utf8=0 -m cli.main`，向标准输入传入以下 SQL 的 UTF-8 字节：

```sql
CREATE TABLE t(id INT,name VARCHAR);
INSERT INTO t(id,name) VALUES(1,'张三');
SELECT name FROM t;
```

实际标准输入编码为 `gbk`；退出码为 0，返回记录为 `寮犱笁`，错误值已通过真实数据库写入链路。此问题影响非 UTF-8 默认环境下的标准输入入口；SQL 文件路径入口显式按 UTF-8 读取，GUI 直接传字符串，不应据此宣称它们同样存在此问题。

建议按命令行接口声明统一标准输入为 UTF-8，或增加明确的输入编码约定和选项，并测试未设置 UTF-8 环境变量的 Windows 管道。现有命令行测试设置 `PYTHONIOENCODING=utf-8`，CI 仅使用 Ubuntu，未覆盖该场景。可临时使用 `python -X utf8 -m cli.main` 保证 UTF-8 管道按正确编码读取。

### 3. P3：记录解码没有检查 VARCHAR 的 255 字节上限

位置：`database_system/storage/record.py:47–49`。

编码器拒绝超过 255 字节的 VARCHAR，解码器只检查字段是否超出整条记录，接受 256 字节字段。复现：

```python
import struct
from storage.record import deserialize
from sql_compiler.ast_nodes import ColumnDef

列 = [ColumnDef("s", "VARCHAR", line=1, column=1)]
记录 = b"\x00" + struct.pack("<H", 256) + b"a" * 256
print(len(deserialize(记录, 列)[0]))  # 当前输出 256
```

这与 `plan/handoff/contracts.md:531` 的物理格式不一致。普通 SQL 写入会拒绝这种值，所以它是损坏或外部构造记录的校验缺口，不是已发现的常规写入丢失问题。建议解码端复用长度上限并补边界测试；不单独作为推送阻断项。

## 编码规范和文档

- `parser.py:289–342`、`storage/record.py:27、43–50` 多处将分支、返回和多条操作压在同一行。建议拆行，便于断点、异常定位和现场讲解；没有证据表明这些写法本身导致错误。
- `query_binding.py`、`engine/relational.py` 的多个函数和内部计划字典缺少完整类型描述。建议逐步补参数/返回类型，必要时引入 TypedDict，降低列下标和计划字段修改风险。
- 基础与扩展类型规则分布在 `types.py`、`query_binding.py`、`query_numbers.py` 等处。后续增加类型时应统一规则入口，避免两套路径行为漂移；本次未据此确认额外结果错误。
- `docs/extensions_validation.md:3` 的“尚未创建提交”是当时工作区验证状态，现已与本地分支状态不同。应明确其历史记录属性，并用提交号标识新的验收记录。
- CI 已运行测试和 SQL 基准，但未配置格式或静态检查。可以后续加入轻量检查；不建议为了审查结果未经讨论直接全库格式化。
- CI 仅覆盖 Ubuntu，建议加入 Windows，尤其应覆盖标准输入编码。测试依赖 `pytest` 未限定版本，可记录已验证版本范围或锁定开发依赖以改善复现。

## 实际验证

| 检查 | 结果 |
|---|---|
| develop 独立源码副本完整 pytest | 567 项通过，25.82 秒 |
| compiler/execution 公共 SQL 输出基准 | 40 组全部通过 |
| 固定种子随机 SQL 文本变异 | 400 次：11 次成功或空输入，389 次受控拒绝，未捕获异常 0 次；不代表随机正确性证明 |
| 独立构造深层括号及长 AND 链 | 两种均复现 RecursionError |
| Windows GBK 默认编码下 UTF-8 管道 | 确认“张三”被存为“寮犱笁”，退出码仍为 0 |
| 损坏记录解码边界 | 确认解码接受 256 字节 VARCHAR，编码端则拒绝 |
| 超长 5000 位整数字面量 | 受控 ParserError，后续查询仍可编译 |
| SQLite 对照支持范围内查询 | 90 条结果一致，覆盖分组聚合、筛选、排序、连接及自连接 |
| 存储专项 | 容量 1、2、64 各插入 160 行，45 轮更新/删除，每轮核对，重开后各 148 行正确 |
| Python 3.11 语法兼容检查 | 53 个 Python 文件通过；不是 Python 3.11 实际运行结果 |
| 提交差异空白检查 | `git diff-tree --check develop^ develop` 通过 |
| 跟踪文件检查 | 未见运行数据库、缓存、日志、虚拟环境或明显密钥内容混入 develop |

独立副本：`database_system/data/audit-develop-685e06d1/`。临时数据库均使用独立、被忽略的数据目录，不操作既有演示库。没有修改已提交测试以取得通过。

完整回归复现方式：在 develop 的干净副本中，从仓库根目录运行 `python -m pytest -q -p no:cacheprovider --basetemp .audit-pytest --tb=short`；从 `database_system` 目录运行 `python -X utf8 -m tools.case_runner --suite all`。

## GitHub 推送判断

- 本地 develop 已有扩展提交；相对现有本地远程跟踪记录领先 1 个提交。未刷新远端，不把该记录当作服务器的实时状态。
- CI 已配置 Ubuntu、Python 3.11/3.13 的 pytest 与全部 SQL 基准；运行实现仅依赖标准库，测试依赖 pytest。
- PPT 文件目前未跟踪；正常 push 不会自动上传未跟踪文件。
- 本次没有实际运行远程 CI、教师隐藏测试或 Linux 环境，因此无法承诺这些环境全部通过。
- 建议先修复问题 1、2，并一并补齐问题 3 的小范围校验；完成针对性回归和完整检查后再推送验收版本。若仅作开发备份，可以推送，但应明确已知问题。
- 若希望 GitHub 的 develop 同时包含桌面界面，需要另行将界面分支合回 develop；当前只完成过 develop 合入界面分支。

## 两个 P2 修复验证

修复日期：2026-09-11。修复基于 `develop@9b5ede0`，本记录随修复一同提交。验证发生在提交与推送之前，结果对应以下实现和回归用例。修复范围为两个 P2，记录解码的 P3 未在本轮修改。

### 标准输入编码

CLI 在读取标准输入前将真实文本流设置为 UTF-8 严格解码，避免 Windows 默认 GBK 或环境替换策略改写输入。无效 UTF-8 在打开数据库前返回 `[IO]` 和退出码 2。StringIO 文本替身、UTF-8 文件及 BOM 保持兼容。

新增 `database_system/tests/test_cli_encoding.py`：12 项真实管道及接口回归，覆盖关闭 Python UTF-8 模式的原生环境、强制 GBK、替换错误策略、中文持久化与重开、无效字节、BOM、中文文件路径和 StringIO。

### 深层表达式

解析器对括号与 NOT 的同一路径混合嵌套设 64 层上限，并在返回 AST 前使用显式栈检查 128 层表达式树深度（叶子为 1 层），覆盖迭代构造的长 AND、OR 和算术链。这样可在 AST 序列化、深复制及优化之前返回带触发位置的 ParserError。没有修改 Python 全局递归上限，也没有用宽泛异常捕获隐藏内部程序错误。

超限语句不执行；run() 通过既有分号恢复机制继续处理后续语句。真实 CLI 保留错误前后的语句输出，并按 SQL 失败返回退出码 1。纯编译入口仍抛 ParserError，不提交真实目录。

新增 `database_system/tests/test_expression_limits.py`：32 项回归，覆盖合法临界值、刚超限的位置、宽而浅的树、未闭合括号、连续 NOT、长条件和算术链、WHERE/ON/HAVING/UPDATE/DELETE、语句间恢复、编译模式、CLI 输出、退出码及重开后的数据。

### 检查结果

| 检查 | 结果 |
|---|---|
| 新增定向回归 | 44 项通过，3.26 秒 |
| 完整回归 | 611 项通过，19.67 秒；原有 567 项继续通过 |
| 公共 SQL 基准 | 40 组全部通过 |
| 本次源码与测试的 Python 3.11 语法检查 | 通过；非 3.11 实际运行结果 |
| Git 差异空白检查 | 通过 |

README、文法和公共契约已写明 UTF-8 输入要求及表达式资源限制。CI 增加 Windows，与 Ubuntu 一起运行 Python 3.11/3.13 测试矩阵；远程流水线尚未触发，不据本地检查声明远程通过。

已解决原审查中的两个 P2。本记录与修复提交至 develop；界面分支尚未同步本轮修复。
