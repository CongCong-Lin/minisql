# 编译模式用例

每个用例由同名 `.sql` 与 `.expected` 组成，使用独立临时数据目录和 `--mode compiler`。预期文件末行为 `EXIT: N`，由用例执行器处理。

C 已提交 `c01_*` 至 `c16_*`，共 16 条：6 成功、10 错误。成功覆盖基础查询、大小写、INSERT 重排、复合布尔条件、ASCII／中文 255 字节；错误覆盖重复表列、未知表列、INSERT 重复／缺列／值数、双类型错、BOOL／FLOAT 插入和 256 字节字符串。

`test_c_public_cases.py` 核对样例语义、AST 结构及 Token 源位置，不是词法、语法、计划或 CLI 的完整验收。完整 stdout 比对须等 A/B/D 实现后运行。不要把 Python 参数化测试数量当成公共 SQL 用例数量。
