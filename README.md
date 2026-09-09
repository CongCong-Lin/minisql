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

当前 A 已完成 Lexer、CLI 输出协议、用例执行器和 CLI 替身测试；Parser、Catalog、页存储、执行器和 runtime 的真实 SQL 链路仍待其他模块完成。本目录未初始化 Git 或关联远程仓库。
