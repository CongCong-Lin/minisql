# MiniSQL 工程

## 当前里程碑

基础 SQL、编译与执行流水线、记录编码、目录及页缓存已经完成集成。当前增加单表 UPDATE、多键 ORDER BY、INNER JOIN、GROUP BY、COUNT／SUM／AVG／MIN／MAX 和 HAVING，详见 [扩展说明](docs/extensions.md)。

## 运行检查

在本目录运行：

```powershell
python -m pip install -r requirements.txt
python -m pytest
```

也可在仓库根目录运行 `python -m pytest`。两处使用同一套测试。

通过 SQL 文件使用，先准备文件中的表结构和数据：

```powershell
python -m cli.main example.sql --mode compiler --data-dir data
python -m cli.main example.sql --mode database --data-dir data
python -m tools.case_runner --suite all
```

`database` 模式执行数据操作，`compiler` 模式展示编译结果并仅提交 CREATE 的目录。每条 SQL 以分号结束。默认展示 Token、AST、原始及优化计划，使用 `--no-tokens --no-ast --no-plan --no-opt-plan` 简化输出。

SQL 文件和标准输入统一使用 UTF-8。通过管道输入时，发送端也应输出 UTF-8 字节；无效编码会返回 `[IO]` 错误和退出码 2，不会打开或修改数据库。

表达式支持最多 64 层括号与 NOT 混合嵌套，表达式树深度最多 128 层（叶子计为 1 层）。超限返回带位置的语法错误，当前语句不执行，后续分号语句继续处理；不会修改 Python 的全局递归设置。

可重复运行的完整演示：

```powershell
python -m tools.extension_demo
python -m tools.extension_demo --details
```

脚本在 `data/` 下创建独立目录，执行 [扩展示例](examples/extensions/)，校验结果并保存日志。已有数据库文件格式兼容；正常关闭或断开连接后保存数据。

测试同时覆盖模块、真实 SQL 链路、公共输出基准和跨进程重启。模块参数化测试不计入团队约定的公共 SQL 用例数量，最新验证结果见 [运行记录](docs/extensions_validation.md)。

## 职责边界

| 成员 | 模块 |
|---|---|
| A | lexer、cli、tools/case_runner、端到端用例 |
| B | parser、ast_nodes、record、storage_engine |
| C | semantic、types、catalog、page、buffer、file_manager |
| D | planner、optimizer、errors、utils/results、executor、evaluator、runtime、持续集成 |

上表记录基础开发归属。当前数据库扩展由负责人独立维护，UI 同伴只在界面分支工作。

AST 构造时位置使用关键字参数，例如 `ColumnDef("id", "INT", line=1, column=16)`。AST 序列化包含结构字段和节点名称，排除语义标注。Token 枚举值和公开名称一致。

`engine.runtime._compile_statement` 是内部单语句连接器。完整扫描、分号恢复、目录提交和执行由 `run()` 承担；纯编译使用 `compile_sql()`，它通过目录快照避免写入实际数据库。已有 UI 使用的内部检查入口保持兼容。

## 开发约定

以 [公共契约](../plan/handoff/contracts.md) 为准，先在自己的分支实现所属模块并补测试，再通过 PR 集成。测试替身不替代真实模块验收。目录及页格式细节见公共契约，不在骨架中维护第二份规则。
