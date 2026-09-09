# MiniSQL

四人协作教学数据库，已建立 M1 骨架，并完成 C 的类型、语义、目录及物理页缓存模块。

- 工程目录：[database_system](database_system/README.md)
- 权威契约：[v1.5](plan/handoff/contracts.md)
- 职责分工：[分工文档](plan/handoff/team_plan.md)
- 当前交接：[交接文档](plan/handoff/handoff.md)
- C 实现与测试：[交付说明](database_system/docs/c_implementation.md)

从仓库根目录运行：

```powershell
python -m pip install -r database_system/requirements.txt
python -m pytest
```

测试覆盖 M1 契约及 C 的模块行为、故障和正常关闭重启。A、B、D 的业务模块仍待实现，完整 SQL 和命令行验收尚未完成；系统目录使用真实行级存储的联调仍待完成。未实现入口明确抛出 `NotImplementedError`。
