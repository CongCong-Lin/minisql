# MiniSQL 工程

## 当前里程碑

M1 已提供公共异常、Token、AST、结果和接口骨架。C 已实现类型、语义、内存／JSON／系统目录后端，以及真实页读写、LRU／FIFO 缓存和空闲链，详见 [C 交付说明](docs/c_implementation.md)。SQL 解析、表级存储、计划和执行器仍为其他成员待实现模块。

## 运行检查

在本目录运行：

```powershell
python -m pip install -r requirements.txt
python -m pytest
```

也可在仓库根目录运行 `python -m pytest`。两处使用同一套测试。

后续完整实现才使用：

```powershell
python -m cli.main example.sql --mode compiler --data-dir data
python -m cli.main example.sql --mode database --data-dir data
python -m tools.case_runner --suite all
```

当前 CLI 和用例执行器仍明确报告 M1 未实现并返回 2。C 已提交 16 条公共 SQL 用例及预期输出，语义结果通过模块核对，完整命令行比对待联调。模块参数化测试不计入课程要求的 60 条 SQL 用例。

## 职责边界

| 成员 | 模块 |
|---|---|
| A | lexer、cli、tools/case_runner、端到端用例 |
| B | parser、ast_nodes、record、storage_engine |
| C | semantic、types、catalog、page、buffer、file_manager |
| D | planner、optimizer、errors、utils/results、executor、evaluator、runtime、持续集成 |

所有公共实现入口均有中文说明。未实现的业务方法抛 `NotImplementedError`，不要把存根返回 None 或空结果作为成功。

AST 构造时位置使用关键字参数，例如 `ColumnDef("id", "INT", line=1, column=16)`。AST 序列化包含结构字段和节点名称，排除语义标注。Token 枚举值和公开名称一致。

`engine.runtime._compile_statement` 是 M1 的内部连接器，只接收单语句 Token，连接语法、语义、计划和优化；它不是公开 SQL 提交入口。完整扫描、分号恢复、目录提交和执行仍由待实现的 `run()` 承担。

## 开发约定

以 [公共契约](../plan/handoff/contracts.md) 为准，先在自己的分支实现所属模块并补测试，再通过 PR 集成。测试替身不替代真实模块验收。目录及页格式细节见公共契约，不在骨架中维护第二份规则。
