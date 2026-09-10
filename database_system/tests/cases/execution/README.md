# 数据库模式用例

每个 `.sql` 配同名 `.expected`。用例执行器使用独立临时目录和 `--mode database`。
持久化重启场景在 `tests/test_integration.py` 中用两个进程复用同一目录。
