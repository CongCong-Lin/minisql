# MiniSQL

教学数据库：已贯通 SQL 编译、执行、页式存储与重启恢复。当前 `develop` 在基础功能上增加 UPDATE、ORDER BY、INNER JOIN、GROUP BY、常用聚合和 HAVING。

- 工程目录：[database_system](database_system/README.md)
- 权威契约：[v1.6](plan/handoff/contracts.md)
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
python -m tools.extension_demo
```

演示逐步运行真实 SQL，并核对排序、更新、连接、聚合、错误恢复和重启结果；每次使用独立数据库。添加 `--details` 可查看 Token、AST 和执行计划。

桌面 UI 由 `feat/sqlyog-ui` 分支独立开发，当前 `develop` 通过命令行和 Python API 使用数据库，后续再合并界面。磁盘格式保持兼容，不提供完整 MySQL、事务或并发服务。
