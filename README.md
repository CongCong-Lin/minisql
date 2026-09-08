# MiniSQL

四人协作教学数据库，当前完成 M1：公共类型、接口存根和契约测试。

- 工程目录：[database_system](database_system/README.md)
- 权威契约：[v1.5](plan/handoff/contracts.md)
- 职责分工：[分工文档](plan/handoff/team_plan.md)
- 当前交接：[交接文档](plan/handoff/handoff.md)

从仓库根目录运行：

```powershell
python -m pip install -r database_system/requirements.txt
python -m pytest
```

测试通过只表示 M1 对接检查通过。SQL 编译、数据持久化和完整命令行尚待实现；生产存根会明确抛出 `NotImplementedError`。本目录未初始化 Git 或关联远程仓库。
