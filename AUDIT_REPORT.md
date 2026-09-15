# MiniSQL 项目问题审查报告（持续更新）

审查范围：`database_system/` 全部源码、测试、仓库卫生、文档一致性。
审查方式：只读。多轮迭代 + 并行子代理分析，主审与子代理发现交叉验证。
约束：本报告是本任务唯一主动新建的交付文档，项目代码零改动；`database_system/data/` 下另有测试运行产生的临时产物（条目 33），非本任务写入。

状态图例：`[P0]` 严重缺陷（正常输入下错误结果/数据丢失）｜`[P1]` 明确 bug 或高风险设计｜`[P2]` 一般问题｜`[P3]` 卫生/规范问题

## 总览

截至当前轮次：**P1 × 11，P2 × 42，P3 × 38**，共 91 条独立发现（编号至 92，其中条目 19 已并入条目 8；条目 2、26 按复核后级别标注但仍保留在原始章节位置）；其中 3 条经契约文档核对后标注"契约声明的设计取舍"（条目 2、16、26），1 条主审误判已自我修正（条目 47），子代理误报 2 条被排除（DROP TABLE 契约矛盾、stdin BOM 不对称）、1 条子代理描述偏差被修正（条目 68 NULL 排序方向），条目 8/19 重复登记已在质量复核轮合并。未发现正常 SQL 输入下产生错误结果的 P0 级行级缺陷；问题集中在**崩溃一致性缺失、空间管理退化、架构契约靠注释维持、语句级原子性缺口、扩展层契约偏离、存储低层 API 信任边界缺失**六个架构层面。611 项测试全部通过（动态验证），问题集中在测试守护范围之外。契约对照（第 3 轮子代理）结论：实现与 contracts.md v1.6 吻合度高，存储格式/协议行/消息文案等硬性条款几乎逐字一致；主要偏离集中在扩展层类型提升（条目 79）与文法接受集私自收窄（条目 80）。

---

## P1 — 明确缺陷或高风险设计

1. [P1] storage_engine.py:142-175 ｜ UPDATE 长记录"先插入后删除"两步无原子性，中途失败产生重复行 ｜ `update_record` 对放不进原槽的新记录先 `insert_record` 再 `delete_record`（173-174 行），无回滚机制。test_extensions.py:429 只注入"第一步 insert 失败"（原记录尚在，安全）；"insert 成功、delete 失败"（IO 故障窗口）会留下新行已入链、旧行未打墓碑的**重复行**状态，无任何标记可供恢复，重启后读到两份数据。extensions.md:38-39 已声明"不承诺 I/O 失败时回滚已写入内容"，但**重复行不是"未回滚的已写内容"而是凭空多出的行**，任何恢复叙事下都属静默数据错误，超出"不承诺回滚"的声明范围；文档对"两步之间失败"的具体后果无任何说明。
2. [P2·契约声明的设计取舍] storage_engine.py:113-136 ｜ 删除空间零复用、无碎片整理，文件只增不减 ｜ `delete_record` 仅把槽目录 offset 置 0xFFFF 墓碑（136 行），`free` 高水位指针从不回退、槽目录永不收缩；insert 页满判断只看 `free + len(encoded) <= PAGE_SIZE - 4*(slots+1)`（68 行），整页全是墓碑也判定页满并 alloc 新页。**handoff.md:86 明示"本版不复用墓碑槽"，属契约声明的设计取舍而非实现 bug**；但与主流 slotted page 实现（空间复用）的差距客观存在，且 free_page/空闲链机制在正常 SQL 流程中无人调用（无 DROP TABLE），长跑负载空间放大无上界，报告保留为最重要的设计债务。
3. [P1] file_manager.py 全文件 ｜ 持久化链路无任何 fsync，且追加半写页使整库不可打开 ｜ 全部写入经 `buffering=0` 原始句柄只到 OS 页缓存，close 也不 fsync；更严重的是 alloc_page 追加路径（170-173 行）写入中途被杀会留下半写页，下次打开在 36-37 行因 `size % PAGE_SIZE` 直接抛"数据库文件长度未按页对齐"，无截断修复或恢复路径——一页半写即整库不可打开。README 声称"页式存储与重启恢复"，实际恢复仅指"正常 close 刷盘 + 重启扫描系统表"，契约文档自己也写明不承诺 I/O 回滚，宣传口径与实现边界不一致。
4. [P1] buffer.py:35-49 + file_manager.py:115-123 ｜ 缓存无 pin 机制，上层持有的 Page 引用可被淘汰后静默失联 ｜ `get_page` 返回缓存内唯一对象，容量满直接淘汰队首，`write_page` 后 `_discard` 移除缓存；"旧引用不得再修改"契约只写在 docstring，无 pin 计数、无失效标记、无 use-after-invalidate 检测。引擎侧已被迫写了两处防御性补丁（storage_engine.py:77、172 的"插入可能触发淘汰；不再使用之前持有的 page 引用"注释），证明风险真实；任何新调用点忘记重新 get_page，对旧引用的修改将静默丢失。
5. [P1] buffer.py:46-49 ｜ 淘汰日志故障升级为 ExecuteError，且抛出点在替换已完成之后 ｜ `_insert` 先完成写回、删除、计数与新页插入（38-44 行），然后才 print 淘汰日志并把 OSError/ValueError 转成 ExecuteError 抛出（46-49 行）——页已换出、缓存状态已变更、统计已更新，但调用方收到"操作失败"；stderr 管道关闭/损坏时，所有触发淘汰的读页请求全部失败。日志故障不应有数据操作级别的失败语义。
6. [P1] file_manager.py:166-173 ｜ 空闲链操作与 meta 更新非原子，崩溃留下不可回收的泄漏页 ｜ alloc_page 复用路径：flush_page(0) 先落盘新链头（167），随后 write_page 清零被分配页（168）；两步之间崩溃则该页既不在空闲链上也不是合法数据页，永久泄漏。注释自认"失败可泄漏页"（166 行）但没有任何泄漏检测/回收机制（如重启时按 page_count 反查未引用页）。追加路径（170-173）同理：写入成功与 `_page_count += 1` 之间中断，页成为孤儿。第 3 轮对抗子代理另发现 INSERT 页分裂的三步跨页非原子变体（storage_engine.py:75-88，见条目 87）。
7. [P1] catalog.py:104-119 ｜ 页存储后端建表三步非原子：中途失败可静默恢复出残缺表结构 ｜ `storage.create_table(name)`（物理表 + 页 0 计数+1）→ 逐列 insert __catalog__ → `flush()`。若某列 insert 中途失败：内存目录不更新（用户视角失败），但物理表页与部分目录行已成脏页，会被后续任意成功语句的 flush 持久化。重启后 `_load_storage`（catalog.py:231-237）只校验"已写入的序号连续"（`sorted(cols) != list(range(len(cols)))`）——**元数据没有总列数或提交标记**（c_review_2026-09-08.md:120-124 同结论），若失败发生在尾部列，序号 0..k-1 恰好连续，恢复出的是**静默残缺的 schema**（如 5 列表恢复成 3 列），后续该表的读写行为不可预期，而非显式报"系统目录损坏"；仅当序号出现空洞时才报损坏。物理映射与系统目录双写无任何两阶段/回滚点，属结构性一致缺口。
8. [P1] executor.py:30 + storage 全层 ｜ 失败语句的半成品脏页会泄漏到后续 flush，多行语句部分提交 ｜ `execute()` 仅在语句成功后 `storage.flush()`；语句中途抛 `ExecuteError` 时已产生的脏页留在缓冲池，下一条成功语句的 flush 会把半成品一并刷盘。**多行 DELETE/UPDATE 的部分提交是现实可触发路径**：`_execute_delete`（executor.py:72-82）先收集再逐行打墓碑，第 k 行失败时前 k-1 行已删；UPDATE 同理（85-100 行），且长记录行的 insert+delete 两步不可回滚。"出错语句不影响已提交结果"只在结果呈现层成立，存储层无 undo，破坏该承诺的持久性语义。
9. [P1] 交付物完整性 ｜ Studio 全套源码磁盘丢失且从未入库，仅剩 pyc ｜ `database_system/studio/`（app/inspect/session）、`tools/studio.py`、`tests/test_studio.py` 只剩 `__pycache__` 里的 pyc，磁盘无任何 .py；`git log --all --diff-filter=D -- '*studio*'` 为空、git ls-files 无记录——从未提交，本地丢失且无版本库副本可恢复。extensions.md:118 仍声称"现有实时检查可继续调用"，与实际不可用状态不符。pyc 反编译或重写是仅存恢复路径。

## P2 — 一般质量问题

### 存储与执行架构

10. [P2] file_manager.py:29-39 ｜ 数据页无校验和/魔数 ｜ 仅页 0 magic（137-139）与文件对齐（36）校验；位翻转、半写、外部截断写页都静默通过，直到结构性校验撞墙才报 corrupt 且无法定位损坏页。
11. [P2] file_manager.py:200-218 ｜ close 时 flush 失败仍关闭句柄 ｜ close 捕获 flush_all 异常后在 finally 中无条件 close，失败页保持脏标志但句柄已释放，修改既未落盘也无法重试，异常只向上传播而数据已不可恢复；异常信息也不列明丢失页号。
12. [P2] file_manager.py:106-113 ｜ read_page/write_page 调试 API 语义陷阱 ｜ read_page 绕过缓存返回磁盘快照（函数名不体现，docstring 仅一句警示），test_storage.py 多处拿它做正确性断言，脏页未刷时读到旧数据——**对抗子代理实测**：insert_record 后（未 flush）read_page 读到槽数=0，flush 后=1，任何基于 read_page 的校验/审计逻辑会静默得出错误结论；write_page 无条件整页覆盖并丢弃旧缓存（"旧脏内容永远不能再写回"），被覆盖页的未刷脏修改永久丢失且无告警（实测坐实）。两接口均无过期提示、无活跃页校验，StorageEngine 虽不暴露它们，但 PageStore 层无任何误用防线。
13. [P2] file_manager.py:132-188 ｜ alloc/free 每次全量遍历并验证整条空闲链 ｜ alloc_page（158）与 free_page（180）每次调 `_walk_free_chain()`，cached=True 经 get_page 把链上每页拉进 LRU，触发淘汰写回、改变替换次序并污染命中率统计。**前轮 c_review_2026-09-08.md:116 已实测平方级增长**：容量 1 下依次复用 50/100/200 页，底层读取 1326/5151/20301 次。
14. [P2] storage_engine.py:50-88,209-217 ｜ insert/delete/update 全链遍历，批量操作 O(n²) 页操作 ｜ 每条 INSERT 从首页沿链走到能容纳的页，每页全量 `_header` 校验；`_belongs` 对每个 rid 全链扫描；executor 批量 DELETE/UPDATE（executor.py:72-100）按行放大。无尾页缓存。
15. [P2] record.py:14-29 ｜ storage 编码层与类型系统能力不对齐 ｜ 类型系统支持 FLOAT/BOOL 字面量（types.py:13-19、semantic.py:50），但列类型白名单（catalog.py:98）与 record 编码分支均只认 INT/VARCHAR，靠 catalog 拦截 CREATE TABLE 兜底；一旦放宽白名单或直调 storage API，INSERT FLOAT 列只会抛 "unsupported column type" 且不含列名/值。
16. [P2] record.py:25-27,47-49 ｜ 反序列化未校验 VARCHAR 长度上限；长度前缀 2 字节为契约设计 ｜ 实测（动态验证）：`deserialize` 接受 256 字节 VARCHAR 字段而编码端拒绝，防御缺口至今存在（develop_audit_2026-09-11.md 同结论：与 contracts.md:531 物理格式不一致）。长度字段 `<H` 为 2 字节是契约 §3 明文（team_plan.md:66"VARCHAR 为 2 字节长度前缀"），属设计取舍，但 255 上限下 1 字节足够，2 字节使每条 VARCHAR 列多耗 1 字节挤占 4076 预算，报告保留为契约层面的空间效率观察。
17. [P2] record.py:15 vs storage_engine.py:118-119,149-150 ｜ 类型防御三套标准并存 ｜ record 排除 bool 但接受 int 子类（isinstance）；delete_record 用 isinstance+显式排除 bool；update_record 用 `type(value) is not int` 连子类一起拒。同一 RecordId/INT 值三处三种判法。
18. [P2] buffer.py:38-44 + file_manager.py:115-123 ｜ 淘汰发生在读盘之后，capacity=1 时缓存退化为抖动 ｜ get_page 先读盘成功才触发淘汰（file_manager.py:121-122），淘汰执行在 buffer.py:38-44，峰值内存 2 页；capacity=1 时每次跨页访问都强制淘汰+写回+重读，每请求两次磁盘操作（契约测试明确覆盖容量 1，属已知边界但代价未被文档提示）。
19. [P2] （已并入条目 8，保留编号占位）｜ 与条目 8 为同一发现的重复登记（质量复核轮发现并合并）｜ "失败语句的半成品脏页泄漏到后续 flush"与条目 8 完全同源；条目 8 定性 P1（含多行 DELETE/UPDATE 部分提交的现实触发路径），本条目不再独立计数。

### 执行层

20. [P2] relational.py:43-52 ｜ NestedLoopJoin 纯 O(n×m) 且无谓词下推 ｜ 连接对每对行全量谓词求值；query_plan（query_binding.py:300-318）把 ON 谓词全部留在 join 节点、WHERE 过滤在 join 之后，优化器不识别等值条件也不下推，大表连接必然笛卡尔级扫描，且 left/right 全物化。
21. [P2] relational.py:20-99 ｜ 全算子物化执行，无流水线，两套扫描实现并存 ｜ SeqScan/Join/Sort/Aggregate 全部物化列表，内存 O(输入+输出)；executor.py 的 `_scan` 与 relational.py 各有一套扫描/过滤实现，行为重复。
22. [P2] evaluator.py:70-71 ｜ 比较运算无运行期类型防线 ｜ `_CMP_FUNCS` 直接用 Python `==`，bool 混入时 True==1 静默判等；类型防线完全前置在 semantic/planner，执行层无二次防护。
23. [P2] relational.py:56-64 ｜ Sort 依赖键值可比较，无运行期兜底 ｜ 排序键直接进 tuple 比较，绑定错误的混合类型键会抛 Python 裸 TypeError 而非 ExecuteError。
24. [P2] types.py:14-17 + evaluator.py:62-69 ｜ 类型规则双轨制 ｜ `expression_type` 只认 INT/VARCHAR；扩展查询的 BIGINT/FLOAT 走 `value_type` 标注 + `query_numbers` 另一套算术。同一类型系统分裂在 types.py 与 query_binding/optimizer 两处，规则漂移风险高（FLOAT 比较旧路径不支持、新路径支持）。

### 编译前端与 CLI

25. [P2] lexer.py:106-130 ｜ 未闭合字符串的错误恢复与注释矛盾 ｜ 注释声称"未闭合字符串跳到当前行尾"（112 行），实际代码遇 `\n` 报错后直接 break 停在换行处，字符串后续行的内容会重新进入主扫描循环被当作 SQL 逐 token 扫描，可能产生连环误导性诊断；实现与注释承诺不一致。
26. [P3·契约要求] cli/main.py:37-40 ｜ 展示开关默认全开 ｜ `--tokens/--ast/--plan/--opt-plan` 默认 True。**handoff.md:94 证实这是契约 §4.5 明文要求（"四个展示开关默认开启"），实现与契约一致，不构成实现缺陷**；但从 CLI 可用性看仍是反直觉默认（extension_demo 不得不显式传 4 个 `--no-*` 关闭），且正负开关对依赖"后出现的参数生效"的隐式语义，报告降级保留。
27. [P2] cli/main.py:118-121 ｜ 顶层异常处理丢弃类型与栈 ｜ `except Exception as exc: print(str(exc))` 把任何编程错误压成一行无类型消息、退出码 2，丢失 traceback，诊断困难。
28. [P2] lexer.py:201-203 + parser.py:325-346 ｜ 负整数字面量在全部上下文语法不可用 ｜ `-` 只注册为二元运算符，literal() 只接受 CONST：`VALUES(-1)`、`SET a = -1`、`WHERE a = -1` 全部报 PARSER Error "unexpected token '-'"（对抗子代理实测）；`-2147483648` 在任何位置都无法书写，INSERT（VALUES 不支持表达式）永远无法写入 INT_MIN，UPDATE 只能用 `0-2147483647-1` 变通（实测可写回 -2147483648）。普通用户写 `WHERE a = -1` 直接语法错——报错明确但属日常 SQL 子集的意外排除，是唯一"正常用户必然踩到"的可用性缺陷。
29. [P2] tests/ ｜ 缺"失败语句半成品脏页被后续语句持久化"的回归测试 ｜ 207 个测试对淘汰失败、写回失败、关库失败等异常路径覆盖细致，但没有用例守护条目 8 的行为；UPDATE 两步法的第二步（delete 失败）故障窗口也无测试（test_extensions.py:429 只覆盖第一步失败）。
30. [P2] tests/ ｜ 无崩溃/断电语义测试 ｜ 重启测试都走正常 close 路径；对"进程未 close 即退出"与半写页场景（条目 3、6）无任何测试，"重启恢复"承诺的边界没有被测试固定。

### 配置与仓库卫生

31. [P2] requirements.txt:1 ｜ 唯一依赖 pytest 未固定版本 ｜ 无版本约束，CI（3.11+3.13）与本地（3.13 + pytest 9.1.1）间主版本升级可能行为漂移。
32. [P2] plan/handoff/handoff.md ｜ 文档内部版本漂移（定性已按免责行软化） ｜ 头部自称 v1.6（2026-09-10），第 2 节仍写三份文档均为 v1.5，而 contracts.md 实际已是 v1.6。**handoff.md:5 已显式声明"下文保留 v1.5 阶段历史说明，其中旧进度和四人协作安排不代表当前扩展状态"，§2 的 v1.5 属于被声明保留的历史段落，不构成疏漏**；保留的观察仅是：§2 的文档清单自身未随 v1.6 升级同步（contracts.md、extensions_validation.md 已在别处更新），首次阅读仍易产生版本困惑。
33. [P2] database_system/data/ ｜ 工作区堆积约 2.6MB 审查/运行产物，4 个目录 ACL 拒绝访问 ｜ audit-*×7、p2-*、demo-check-* 等共约 2.6MB（全部未被 git 跟踪，属本地卫生）；`.audit-pytest`、`p2-full-*`、`p2-targeted-*` 拒绝访问（os error 5），阻断 rg/glob 遍历，直接干扰自动化工具。
34. [P2] tests/test_runtime.py:432-434 ｜ 唯一零断言测试 ｜ close_database 编译模式 no-op 测试只调用不验证，与文件内其他强断言风格脱节。

## P3 — 卫生、规范与文档

35. [P3] record.py:30 + page.py:5 ｜ 4076 记录上限与 4096 页大小强耦合的魔法数字 ｜ 4076 = PAGE_SIZE - 16(页头) - 4(单槽) 无推导、无共享常量；PAGE_SIZE 硬编码 4096，"可变页大小"需求未实现，改一处需同步至少四处。
36. [P3] file_manager.py:10,138 + storage_engine.py:44-47 ｜ 头部布局魔法数字散布 ｜ 偏移 6/8、映射区起点 16、槽宽 68、名长 64、表上限 60 在两层重复硬编码，未从单一布局常量取值。
37. [P3] buffer.py:7 ｜ 底层 storage 反向依赖上层 sql_compiler.errors.ExecuteError，层次倒置；storage/__init__.py 无公共 API 导出，外部全部深路径导入。
38. [P3] buffer.py:5,46-49 ｜ 用 print 到 stderr 做运行时日志，应接入 logging；且该日志失败被升级为数据错误（条目 5）双重不当。
39. [P3] 每条语句（含只读 SELECT）无条件 flush_all（executor.py:30）｜ 依赖"只读不置脏"的隐式不变式，只读路径也遍历全部脏页。
40. [P3] storage_engine.py:122-127 ｜ delete_record 重复校验 slot<0，`_header` 返回值被丢弃再手工 unpack。
41. [P3] 类型防御与风格不统一 ｜ `type(x) is not int` 与 isinstance 混用（file_manager.py:59、buffer.py:15 vs record.py:15）；record.py:27 单行分号串联两条语句；storage/__init__.py 无公共 API 导出。
42. [P3] parser.py:328,332 ｜ 错误消息引用不存在的词法类型名 ｜ expected 集合写 `INTEGER_CONST`/`FLOAT_CONST`/`STRING_CONST`，而本 lexer 的 TokenType 枚举只有 `CONST`，用户按消息找不到对应概念。
43. [P3] cli/main.py:100 ｜ 默认数据目录相对当前工作目录 ｜ `Path.cwd() / "data"` 使数据库位置取决于启动目录，换目录启动即"丢库"；建议默认锚定到包根。
44. [P3] errors.py:67-73 ｜ ExecuteError 无源码位置 ｜ 执行期错误在语义层有绑定位置却不携带行列信息，与编译错误体验不一致。
45. [P3] query_binding.py:146-147 ｜ 聚合误用报错文案错位 ｜ ORDER BY/HAVING 上下文误用聚合时统一抛"WHERE、ON 和更新表达式中不能使用聚合函数"，描述误导。
46. [P3] lexer.py / grammar.md ｜ 字符串字面量无法包含换行符 ｜ 转义规则只有 `''`，无换行转义；SQL 标准与 SQLite/MySQL 允许字符串跨行，本实现永远无法存储含换行文本，文档未声明。
47. [P3] storage_engine.py:32-36 ｜ 表上限语义未在代码注释说明（主审误判修正）｜ `count >= 60` 的 count **含系统表**，即最多 59 张用户表，与契约 handoff.md:87（"60 个表映射条目，包含系统表，最多 59 张用户表"）及报错文案 "59 user tables" **完全一致，不构成缺陷**；保留的观察是：代码与 docstring 均未说明 count 计入系统表，首次阅读容易误判 off-by-one。
48. [P3] tools/extension_demo.py:23 ｜ collect_output 假定 ROW 之前必有 COLUMNS，脆弱协议解析。
49. [P3] tests/d_support.py:32-43 ｜ ensure_arithmetic 的 monkeypatch 分支为存根期死代码；tests/test_record.py:25-28,50-54 两处 pytest.raises 缺 match；test_catalog.py:217-232 ｜ JSON 临时文件只在"构造失败"与"replace 失败"两个阶段参数化，**临时文件正文写入、flush、关闭阶段的失败仍无测试**（c_review_2026-09-08.md:129 提出的缺口至今未补）。
50. [P3] database_system/README.md:21-22 ｜ 示例命令引用不存在的 example.sql，文档未说明需自备。
51. [P3] .gitignore ｜ 缺 `*.db` 与根级 `data/` 覆盖 ｜ 仓库根直跑 CLI 会生成未忽略的 data/minisql.db；.gitignore 仅 127 字节。
52. [P3] plan/handoff/handoff.md ｜ 文档版本漂移之外的未跟踪遗留 ｜ 根目录 ppt/ 未跟踪遗留；origin 上 4 个已全量合入 develop 的特性分支（c-implementation、part-B、d/planner-runtime、codex/upload-*）残留未清理，仅 feat/sqlyog-ui 领先 2 提交（与文档一致）。本报告（AUDIT_REPORT.md）同为未跟踪文件，但属本审查的交付物而非遗留。
53. [P3] storage_engine.py:201-207 ｜ 槽位上限 1020 与记录最小尺寸未联动校验 ｜ 防御校验的宽松余量（实际每行至少 4 字节到不了 1020 槽），非缺陷，记录备查。
54. [P3] storage/__init__.py ｜ 包无公共 API 导出，外部全部深路径导入（与条目 37、41 记录重复，独立保留便于检索）。

## 对照主流教学数据库实现的差距（参考 CMU 15-445 BusTub、SimpleDB 类项目）

55. [P2] 全项目 ｜ 无索引结构（B+ 树），一切查询全表扫描 ｜ 同类教学项目（CMU 15-445 Project 2、QuillSQL、SimpleDB）均以 B+Tree 为核心交付物；本 grammar 无 CREATE INDEX，扫描/连接/过滤全部线性扫描，功能面差距最大的一项。
56. [P2] buffer.py ｜ 缓冲池无 pin/unpin、无 LRU-K/Clock 抗扫描污染策略（仅 LRU/FIFO）｜ 主流教学缓冲池（CMU 15-445 P1 标准）要求页固定计数防扫描污染；本项目长表扫描会无差别挤掉热页（页 0 元数据页也会被淘汰重读）。
57. [P2] 全项目 ｜ 无 WAL/undo/redo，崩溃一致性无机制 ｜ SimpleDB 等教学库至少含写前日志与恢复演练；README 的"重启恢复"实为"正常关闭刷盘 + 系统表重启扫描"，与主流认知的"恢复"不符（对应条目 3、6、8、30）。
58. [P3] engine/ ｜ 执行模型为全物化而非火山/向量化迭代器 ｜ 大结果集内存峰值高。
59. [P2] 全项目 ｜ 无并发基础设施且无线程安全声明 ｜ BufferPool 的 OrderedDict 与 PageStore 文件句柄均非线程安全，代码无单线程假设的注释或运行时检查；文档声明单进程，公开 API 层无防护。**已实测升级**：同一进程内打开两个 PageStore 句柄指向同一 .db 文件，两边的页 0 缓存互不失效、互相覆盖——句柄 B 建表 t 并写入 20 行落盘后，句柄 A 再建另一表，重启后 t 表从目录中消失（映射被 A 的陈旧页 0 覆盖，20 行数据成孤儿页），全程无任何报错；SQLite 以文件锁防止此场景，本项目对重复打开毫无检测。

## 第 2 轮 engine/cli/tools 子代理补充发现（已交叉验证合并）

60. [P2] engine/executor.py:116 vs relational.py:42,50 ｜ DELETE/UPDATE 与 SELECT 的谓词判定语义不一致 ｜ executor._scan 用 `if evaluate(...)` 真值判断（None 视为假），relational 用 `is True` 严格判断；当前靠 semantic 强制谓词 BOOL 才未出事，两条路径对非严格布尔值的容忍度不同。
61. [P2] query_numbers.py:20 ｜ 除零判断 `right == 0` 可被 bool 穿透 ｜ False==0 成立，执行层无类型断言；与 types.py:35 的 `type(left) is not int` 防御强度不一致。
62. [P2] types.py:33-54 + query_numbers.py:18-45 ｜ 除法向零截断逻辑双源实现 ｜ abs//abs+符号定号在 32/64 位入口各写一份，错误消息中英文混排（"整数运算的操作数必须为 INT" vs "integer arithmetic out of range"），语义修改需双处同步。
63. [P2] cli/main.py:118-121 ｜ KeyboardInterrupt 绕过退出码契约 ｜ Ctrl+C 是 BaseException 不被捕获，裸 traceback 退出，绕过 0/1/2 契约。
64. [P2] runtime.py:271-276 ｜ 语句执行只捕获 ExecuteError ｜ struct.error、KeyError 等未映射异常直接冒出 run()，后续语句全部不执行，违背逐段错误恢复语义（与条目 8 的脏页泄漏相互独立：一个是持久化语义，一个是执行中断语义）。
65. [P2] runtime.py:39 ｜ 新库判定与 PageStore 实际创建逻辑双轨 ｜ open_database 先判 is_new 再由 PageStore 以 x+b/r+b 决定真实行为，两次判定间可被外部改变；建议以 PageStore 初始化结果为准。
66. [P2] tools/case_runner.py:57-62 ｜ 子进程无 timeout 且 strict UTF-8 解码错误未捕获 ｜ CLI 挂起则 runner 永久阻塞；非法输出抛 UnicodeDecodeError 不在 except OSError 内。
67. [P2] tools/extension_demo.py:43-48 ｜ 每次运行永久新建演示目录且从不清理 ｜ 时间戳+uuid4 命名，重复演示无限堆积 minisql.db 与 execution.log。
68. [P3] relational.py:62 ｜ NULL 排序语义与主流默认不一致（子代理结论修正后采纳）｜ 实测（动态验证）：ASC 时 NULL 排最前、DESC 时排最后（reverse 作用于整个键元组，含 None 标志位）。**修正：extensions.md:52 实际已文档化该语义（"空值升序在前、降序在后"），与实测一致**；未覆盖的是 grammar.md 与 README 主文档，用户只读文法看不到该语义；与 PostgreSQL 默认（ASC NULLS LAST）相反、与 SQLite 一致。
69. [P2] relational.py:75-99 ｜ 分组键依赖 Python 哈希相等（True==1==1.0），类型防线失效时静默并组 ｜ 当前靠列类型单一性兜底，代码无防御，并组错误将是静默的。
70. [P2] storage_engine.py:124,189-195 ｜ delete_record 每行 _belongs 全链遍历 + 每次重查元数据页 ｜ 批量 DELETE/UPDATE 为 O(N×链长)（补充条目 14 的逐行细节）。
71. [P3] evaluator.py:64 ｜ 逐行求值热路径内做函数级 import（应上移模块顶部）；evaluator.py:7-14 与 optimizer.py:10-17 的 `_CMP_FUNCS` 比较函数表重复定义，语义修改需双处同步。
72. [P3] runtime.py:141 ｜ 补造的全局 EOF 位置硬编码 (1,1)，lexer 异常路径诊断定位错乱；runtime.py:190 ｜ `_segment_index` 开区间判断使错误恰落在分号上时归段错误；runtime.py:92 ｜ origin is None 时伪造 PLANNER 错误兜底，掩盖真实失败阶段；runtime.py:64 ｜ origin 变量解包后未使用。
73. [P3] runtime.py:100-115 与 213-255 ｜ `_compile_statement`（M1 测试入口）与 `_compile_segment` 两套分阶段编译连接逻辑并存，阶段增改需双处同步。
74. [P3] executor.py:87,94 + storage_engine.py:145 ｜ UPDATE 每行 serialize 两次（预检结果丢弃，update_record 内部重编）；executor.py:103-119 ｜ `_scan` 每层全量物化 list，放弃 scan_records 生成器惰性。
75. [P3] catalog.py:123 ｜ find_table 每次调用 deepcopy 整个 schema，执行器热路径（每次 _require_table/scan）产生大量短命对象，可用冻结视图替代。
76. [P3] relational.py:95-98 ｜ 聚合输出列名硬编码 group_N/aggregate_N，依赖外层 Project 提供 label，计划被单独消费时暴露无意义表头。

## 文档一致性结论（除下列例外项外全部对上）

77. [P3] 文档一致性总体良好（结论）｜ 保留字 23 个、嵌套 64/深度 128 与 lexer.py:38-43、parser.py:16-17 精确一致；README/扩展文档引用的函数、AST 节点、文件均真实存在；test_m1_contracts.py 从 contracts.md 动态解析签名做契约防漂移校验属工程亮点；根/工程双 pytest.ini 等价无冲突；CI workflow 路径有效。例外：handoff.md 头部 v1.6 与 §2 的 v1.5 引用漂移（条目 32 另记）；contracts.md 自身修订记录清单止于 v1.5（第 7 行），v1.6 变更仅写在导言，版本轨迹在文档内部分裂为两处；database_system/README.md:21-22 示例引用不存在的 example.sql（条目 50 另记）；extensions.md:118 对 Studio 的描述与源码丢失现状不符（条目 9）；docs/extensions_validation.md:3 的"尚未创建提交或推送"为历史时态，与当前 develop 已含扩展提交的状态不符（develop_audit_2026-09-11.md:110 同结论），应标注历史记录属性或以提交号更新。
78. [P2] tests/cases/ ｜ 公共用例库 40 条，未达契约"总计至少 60 条"验收门槛 ｜ 实测 compiler 20 条 + execution 20 条 = 40 条 .sql 用例；team_plan.md:141 与 handoff.md:98 均要求"每人至少 15 条、总计至少 60 条"（contracts.md:501），且 compiler 套件 20 对也无法满足"第一阶段验收时每人至少 15 条 compiler 用例"的 4 人分布。错误用例 22/40（55%）满足"不少于一半"的比例要求，但总数缺口 20 条；develop_audit_2026-09-11.md 记录的"公共 SQL 输出基准 40 组"同样止步于 40。按契约口径这是未完成的验收项，而非可选优化。

## 第 3 轮契约对照子代理发现（contracts.md v1.6 逐节对照，已合并）

79. [P1] query_binding.py:198-202 + evaluator.py:63-65 ｜ 分组/聚合扩展路径的普通 INT 算术被提升为 BIGINT，绕过契约 32 位溢出检查 ｜ 契约 §11.4（contracts.md:756）明文"普通整数表达式仍按 32 位规则执行"（§5.3 要求逐步溢出检查），但分组/聚合查询的 SELECT/HAVING/ORDER BY 中不含聚合的普通 INT 算术（如 `a+1`）经 query_binding 绑定后 value_type 被提升为 BIGINT 并按 64 位求值。**已实测**：`SELECT a FROM t GROUP BY a HAVING a+2147483647 > 0`（a=1）计划中该加法 value_type=BIGINT，求值 1+2147483647 不报 "integer arithmetic out of range"，同一表达式在 WHERE 中会报错——同一表达式在两条路径行为分裂，且 INT_MAX 边界附近的算术在分组查询中静默失真。
80. [P1] parser.py:243-244 + grammar.md:51 ｜ 隐式表别名接受集被私自收窄，契约未授权 ｜ 契约文法（contracts.md:105）`table_ref -> IDENTIFIER [ [ AS ] IDENTIFIER ]` 允许任意 IDENTIFIER 作隐式别名（§10:718 要求 grammar.md 与之一致）；实现额外阻塞 LEFT/RIGHT/FULL/CROSS/NATURAL/USING/LIMIT/UNION 八个词（实测 `SELECT * FROM t LEFT;` 报 unexpected token 'LEFT'，普通标识符别名正常）。grammar.md:51 自行声明"隐式表别名避开不支持的连接修饰词"，但该收窄在契约 v1.6 修订记录中无授权——文法接受集属契约管辖范围，私自收窄改变了合法程序集合。
81. [P2] semantic.py:58,65,123（另见 97,104,108,113,118,140）｜ 语义诊断全量中文，与契约 §7.2 英文示例系统性漂移 ｜ 契约 §7.2（contracts.md:373-379）给出三种语义原因英文示例文案（`column 'score' does not exist in table 'student'`、`operator '+' cannot be applied to INT and VARCHAR`、`column 'id' expects INT, but VARCHAR found`），实现全部为中文自由措辞（"列不存在：{name}"、"运算符 {op} 不支持类型 {l} 和 {r}"、"列 {name} 需要 {wanted}，实际为 {t}"）。docs/extensions.md:123 表明系有意偏离，但 contracts.md v1.6 修订记录未含此变更——验收测试若按契约文案断言将系统性失败，属契约与实现未对齐的漂移。
82. [P3] contracts.md:426-450 ｜ 契约工程目录树落后于实际文件树 ｜ 实际存在但契约目录树缺失：sql_compiler/query_binding.py、engine/relational.py、engine/query_numbers.py、tools/extension_demo.py、docs/（3 个文档）、examples/extensions/、tests/fixtures/v1_database.json、tests/d_support.py。契约作为交接基准未反映真实结构。
83. [P3] lexer.py:169-178 ｜ `.5` 的失败形态偏离契约词法 ｜ 契约词法（contracts.md:57）点号是分隔符，`.5` 应被切分为 DELIMITER('.') + CONST('5') 两个 Token（随后在语法阶段失败）；实现把 `.5` 整体吸收为一条词法错误 invalid numeric literal。失败阶段与诊断形态均与契约不同（grammar.md:52 亦自行声明"不接受 .5"，同样无契约授权）；契约只约定了合法输入的词法行为，此差异影响错误报告的一致性而非功能。
84. [P3] semantic.py:71-72 ｜ DELETE 的 WHERE 中出现聚合时报含混的"未知表达式节点" ｜ 基础表达式分析器无 AggregateExpr 分支，DELETE WHERE 中的聚合走到兜底报错，与扩展路径的专门诊断（query_binding.py:147）不一致；契约 §11.4 仅禁止 WHERE/ON/UPDATE 中聚合，未明确 DELETE 场景，诊断体验与契约的错误格式要求（§7.2）脱节。

## 第 4 轮对抗性输入与 API 误用子代理发现（已交叉验证合并）

85. [P1] storage_engine.py:173-175 + file_manager.py:175-188 ｜ free_page 不校验目标页是否仍被表链引用，alloc 复用后产生跨表串页（对抗子代理实测） ｜ 实测：建表 A（页 1，记录 777）→ free_page 回收该页 → create_table('B') 复用该页并写入 999 → `scan_records('A')` 返回 `(999,)`——同类型列时表 A 完全静默读出表 B 的数据，无任何报错；继续 `delete_record('A', 旧rid)` 通过 `_belongs` 校验后误删 B 的槽，B 的数据变空。跨表污染 + 静默数据错乱 + 误删链式发生。缓解因素：正常 SQL 流程无 DROP TABLE，free_page 无人调用（条目 2），该风险仅在直接调用存储低层 API 时触发——但 free_page 本身无任何"目标页仍在表链上"的校验，属于存储信任边界的最大缺口，与 free/write/read 三接口"无活跃页校验"的系统性问题同源。

86. [P2] storage_engine.py:110 + executor.py:72-100 ｜ 单行字节损坏 = 全表锁死：坏行所在 SELECT/DELETE/UPDATE 全部中断且坏行无法删除 ｜ 单条记录的保留头字节非 0 时 scan 抛 "invalid record header"，该行所在的 SELECT/DELETE/UPDATE 全部在扫描阶段中断（对抗子代理实测：同引擎 INSERT 正常）；executor 先物化扫描再删改（executor.py:72-100），DELETE 收集 targets 时即中断，坏行永远无法通过 SQL 移除，无任何修复/跳过机制——单行磁盘损坏即锁死整表全部读改路径。触发面：磁盘故障、外部字节修改（条目 10 的校验和缺失使其无法定位损坏页）。

87. [P2] storage_engine.py:75-88 + file_manager.py:194-198 ｜ INSERT 页分裂三步跨页非原子（崩溃窗口新变体） ｜ 写旧页 next → 初始化新页头 → 写数据槽，三步跨两页，flush_all 按缓存插入序写回且顺序不定；崩溃后可出现孤儿页或页链指向清零页（重启报 "corrupt data page" 或行静默消失）。与条目 6（alloc 两步）、条目 1（UPDATE 两步）、条目 7（建表三步）同属崩溃一致性缺口家族，此变体前轮审查未记录。

88. [P3] storage_engine.py:173-175 ｜ UPDATE 变长迁移改变无 ORDER BY 查询的行序且不可还原 ｜ 变长迁移（insert+delete）使该行物理移到表尾：实测行序 (1,2,3) 更新第 2 行为长串后变 (1,3,2)，再次改回短值仍保持 (1,3,2)——行序语义随写入历史漂移，依赖行序的应用（分页/快照对比）受影响。

89. [P3] storage_engine.py:173-175 + executor.py:96-97 ｜ 变长迁移后 rid 变化，executor 丢弃 update_record 返回的 new_rid ｜ 直接 API 用户持有旧 rid 再操作报 "cannot update a deleted record"；rid 可能变化这一行为未在任何接口层提示，executor 层当前无影响（每行现算现用），但 API 契约暗藏陷阱。

90. [P3] relational.py:251-255 ｜ JOIN 查询表头带表名前缀，单表裸名，同列改写 FROM 后表头格式漂移 ｜ 实测：单表 `SELECT *` 列名 ['a','b']，自连接后 ['agg.grp','agg.val','b.grp','b.val']——输出契约随查询写法漂移，下游按列名消费结果时行为不稳定。

91. [P3] storage_engine.py:130 vs 163-164 ｜ 删除幂等与更新防护语义不一致 ｜ 重复 delete 已删除行静默 return，update 已删除行 raise——两种防护策略并存，API 语义不一致。

92. [P3] lexer.py:141-143 ｜ 超长标识符诊断携带完整词素，报错行可任意膨胀 ｜ 实测 65 字符标识符报错携带全部 65 字符，1000 字符词素同样全量进诊断——超长输入会膨胀诊断输出（与资源边界保护精神相悖，非安全边界）。



## 动态验证（纯内存，未触碰项目文件）

- 分组路径 32 位溢出绕过（条目 79，契约对照子代理实测）：`SELECT a FROM t GROUP BY a HAVING a+2147483647 > 0`（a=1）不报 integer arithmetic out of range，计划中该加法 value_type=BIGINT；同一表达式在 WHERE 中正确报错——HAVING 与 WHERE 行为分裂确证。
- `deserialize` 接受 256 字节 VARCHAR（条目 16）：构造 `b"\x00"+pack('<H',256)+b'a'*256` 实测解码成功返回 256 字符，官方审计缺口至今仍在。
- 比较函数 bool 穿透（条目 22）：`True == 1` 在比较 lambda 下返回 True，执行层确无类型防线（常态下被 semantic 前置拦截，属防御缺失而非现实错误）。
- 分组键哈希相等（条目 69 实测）：`(True,)`、`(1,)`、`(1.0,)` 作为 dict 键互等，同一组——类型防线失效时并组是静默的。
- 除零检查（条目 61 修正）：`query_arithmetic('/', 10, False, 'BIGINT')` 实际抛"division by zero"——False 被当 0 拦截，但 `True` 会被当 1 静默参与运算，"bool 可穿透"的准确说法是 True 穿透、False 变除零错误。
- NULL 排序方向（条目 68 实测）：单键列 `[3, None, 1, 2]`，ASC 输出 `None,1,2,3`，DESC 输出 `3,2,1,None`——NULL 朝同一物理端（ASC 最前、DESC 最后），与 SQLite 一致、与 PostgreSQL 的 ASC NULLS LAST 默认相反，未写入 grammar.md。
- 双句柄静默覆盖（条目 59 实测）：同进程两个 PageStore 打开同一 .db，句柄 B 建表 t 写 20 行并落盘后，句柄 A 建另一表，重开后 `has_table('t') == False`（映射被 A 的陈旧页 0 覆盖），无任何报错；t 的数据页成为孤儿。
- 半写页整库不可打开（条目 3 实测）：正常 3 页库截断 300 字节模拟断电半写，重开立即抛 ExecuteError"数据库文件长度未按页对齐"，无截断修复路径，整库不可打开确证。
- 跨表串页（条目 85，对抗子代理实测）：free_page 回收表 A 首页后 create_table('B') 复用，表 A 扫描静默读出表 B 数据，且 delete_record('A', rid) 通过 _belongs 校验误删 B 的槽——全程无报错。
- 负数字面量全上下文语法错误（条目 28，对抗子代理实测）：`VALUES(-1)`、`SET a=-1`、`WHERE a=-1` 全部 PARSER Error；UPDATE 用 `0-2147483647-1` 实测写回 INT_MIN 成功。
- 单行损坏全表锁死（条目 86，对抗子代理实测）：改坏单条记录头后 SELECT/DELETE/UPDATE 全部在扫描阶段中断，DELETE 无法移除坏行，同引擎 INSERT 仍正常。
- read_page 静默过期（条目 12，对抗子代理实测）：insert 后未 flush 读到槽数 0，flush 后 1。

## 已验证无问题（排查过并排除的疑点）

- 扩展查询绕过系统表拦截的疑点：query_binding.py:94-95 的 `_source` 补上了 `__catalog__` 访问拒绝，基础与扩展两条路径都覆盖。
- INSERT 部分列/重复列导致 KeyError 逃逸的疑点：semantic.py:107-118 明确拦截（列数一致、重复拒绝、完整覆盖），无法到达 executor 的 KeyError 分支。
- `__pycache__`/.db/data 产物是否被提交：git ls-files 证实零生成物入库，.gitignore 生效，仅工作区堆积（条目 33）。
- 单条记录超页大小导致 insert 死循环分配页的疑点：record.py:30 的 4076 上限恰好封住（与页几何强耦合但行为正确）。
- evaluator/relational 的 AND/OR/NOT 三值逻辑：逐条对照 SQL 三值逻辑表验证正确。
- 64 层嵌套 / 128 层树深限制：实现与 grammar.md 契约一致，含显式栈防递归溢出。
- 词法/语法对 `1.2.3`、`.5`、`12abc`、`==`、裸 `!` 等病态输入的整体消费策略：正确（整词报错不拆分）。
- 病态输入防护密度（对抗子代理实测清单）：标识符 64/65、VARCHAR 255/256 字节（含全角字节计数）边界正确；嵌套聚合、HAVING 未知别名、ORDER BY 别名列歧义、GROUP BY 遗漏、WHERE 聚合均被语义层显式拒绝；63 层括号通过/65 层拒绝、130 长 AND 链触发树深 128 上限；字符串内分号/引号转义、注释截断、BOM 处理符合契约；WHERE 算术溢出/除零为显式 ExecuteError（严格 32 位契约），UPDATE 预检失败时零行写入（原子）。
- 契约-代码一致性：test_m1_contracts.py 动态解析 contracts.md 校验签名，自动化防漂移。
- 子代理误报修正一（DROP TABLE 契约矛盾）：全仓库 grep 证实只有 grammar.md:58 声明**不支持** DROP TABLE，无任何文档声明支持 DROP，不构成契约矛盾。
- 子代理误报修正（stdin BOM 不对称）：lexer.py:34-35 在文本开头剥离 \ufeff，stdin 与文件路径的 BOM 处理实际对称（都只剥开头 BOM），不构成缺陷。
- 子代理结论修正（NULL 排序语义）：子代理称"NULL 恒排最后"，实际为 ASC 时 NULL 最前、DESC 时最后（条目 68），核心问题"未文档化"成立。
- 契约逐节对照核对一致的要点（第 3 轮子代理抽查实证）：§1 词法规则与聚合 LexerError、§2.3 分段/局部 EOF/恢复坐标、§3.1 资源边界 64/128、§3.2 AST 快照、§4.1 字面量转换、§4.2/§9.5 Catalog 与系统目录（60 条目/68 字节、ordinal 从 0、损坏判定）、§4.3 提交时点、§5.3 checked_int_arithmetic 与除零/越界文案、§6.2/6.3 优化规则（实测 Filter[1=1 AND age>10+8]→Filter[age>18] 且返回两种规则、输入计划不变）、§7.2/§7.3 错误格式与结果表、§8.2 CLI 协议行/JSON 参数/退出码、§8.3 runner 语义、§9.1-9.3 页格式全部数值（4096/16B 头/槽 4096-4(i+1)/MSQL/59 张用户表文案逐字一致）、§9.4 表级接口与执行消息、§9.4.1 编排、§9.6 求值、§11.1-11.5 扩展条款主体（64 位/AVG/三值逻辑/别名歧义）；契约引用的 team_plan.md、handoff.md、extensions.md、extensions_validation.md、extensions_ui_samples.json、tests/fixtures/v1_database.json 全部存在。
- 交叉验证：plan/reviews/develop_audit_2026-09-11.md（仓库自带的前轮审查）确认过本报告条目 16 的 deserialize 255 字节缺口（给出复现：解码接受 256 字节字段而编码端拒绝，与 contracts.md:531 物理格式不一致），并证实其"普通 SQL 写入会拒绝该值，属外部构造记录的校验缺口"的定性；该审查的两个 P2（深层表达式 RecursionError、Windows stdin GBK）已在后续提交修复，对应现在的 test_expression_limits.py（32 项）与 test_cli_encoding.py（12 项）。前轮审查还做过 400 次固定种子 SQL 变异（0 次未捕获异常）与 90 条 SQLite 对照查询（结果全一致），与本报告"防御纵深扎实、无 P0 行级缺陷"的判断互证。
- 动态验证（本审查轮）：从仓库根完整运行 `python -m pytest -q`，**611 项全部通过**（22.33s，退出码 0），与官方审查记录的修复后回归数一致；测试全部通过进一步佐证问题集中在测试未守护的架构层（崩溃一致性、空间管理、原子性），而非行级正确性。

## 审查轮次记录

- 第 1 轮（完成）：全模块并行审查 —— sql_compiler（主审完成）/ storage（子代理完成并合并）/ engine+cli+tools（子代理完成并合并）/ 测试与仓库卫生（子代理完成并合并）。
- 第 2 轮（完成）：对抗性场景推演 + 主流实现对照（CMU 15-445、SimpleDB 类教学库）+ 三个子代理发现全部交叉验证（修正 2 条误报、1 条描述偏差）。
- 第 3 轮（完成）：契约文档核对（handoff.md/team_plan.md/c_review/develop_audit 四份前轮材料交叉验证，修正 4 条定性）+ 6 项内存级动态验证（bool 穿透、分组并组、NULL 排序、双句柄覆盖、半写页、CLI 端到端）+ 用例门槛核对（40/60）+ 文档全文对照 + 契约逐节对照子代理返回（contracts.md v1.6 762 行逐节核对，新增条目 79-84，1 条并入条目 78、1 条并入条目 77）+ 对抗性输入子代理返回（病态 SQL/存储 API 误用实测，新增条目 85-92，3 条并入条目 12/28/6）。
- 质量复核轮（完成）：独立编辑视角审查报告本体——发现并合并条目 8/19 重复（统计 78→77）、修正条目 57 引用目标（9→8）、章节计数、BusTub 拼写、条目 32 补引 handoff.md:5 免责行、行号定位补充、口径澄清；40+ 处文件/行号引用抽查全部命中，动态验证数字全部可复现核对。
