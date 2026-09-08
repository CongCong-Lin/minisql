# 数据库模式用例

后续放置同名的 `.sql` 与 `.expected`，每个用例使用独立临时数据目录和 `--mode database`。
持久化重启场景在同一 pytest 用例中复用临时目录。
