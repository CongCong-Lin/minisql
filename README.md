# MiniSQL

教学数据库：已贯通 SQL 编译、执行、页式存储与桌面界面。在 UPDATE、ORDER BY、INNER JOIN、GROUP BY、聚合和 HAVING 基础上，增加空值、新类型、B+ 树索引、代价模型、EXPLAIN、事务、并发控制和访问控制，详见[八项扩展说明](database_system/docs/optional_extensions.md)。

- 工程目录：[database_system](database_system/README.md)
- 权威契约：[公共契约与第二版补充](plan/handoff/contracts.md)
- 职责分工：[分工文档](plan/handoff/team_plan.md)
- 当前交接：[交接文档](plan/handoff/handoff.md)
- C 实现与测试：[交付说明](database_system/docs/c_implementation.md)
- 可选扩展与 UI 对接：[扩展说明](database_system/docs/extensions.md)

从仓库根目录运行：

```powershell
python -m pip install -r database_system/requirements.txt
python -m pytest
```

运行扩展演示：

```powershell
cd database_system
python -m tools.optional_demo
python -m tools.studio
```

演示逐步运行真实 SQL，核对类型、空值、索引选择、提交回滚、并发锁、授权及重开结果；每次使用独立数据库。原查询扩展演示仍可用 `python -m tools.extension_demo` 运行。

当前实现分支为 `codex/optional-extensions`，从 `feat/sqlyog-ui` 建立，支持命令行、Python 会话和桌面界面。新库使用第二版格式，旧库需关闭客户端后执行 `python -m tools.migrate 旧目录 新目录` 复制迁移；源库保持不变。仅支持本机磁盘客户端，不提供网络数据库服务。
