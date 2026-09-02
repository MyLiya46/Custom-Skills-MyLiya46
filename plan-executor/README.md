# plan-executor

版本：v1.2.0（beta）

plan-generator 的下游执行器：先读取 docs/todo.json，按状态和 blockedBy 筛选就绪任务，再按需读取对应 plan，用并行 subagent 执行已评审任务，跑到验收命令全绿后回写 completed，失败隔离为 blocked。

## 目录结构

    plan-executor/
    ├── SKILL.md
    ├── README.md
    ├── references/
    │   ├── parallel-scheduling.md
    │   └── runtime-supervision.md
    └── scripts/
        └── run_state.py

## 使用

前置：先在项目目录用 plan-generator 产出 docs/todo.json 和 docs/plans/，并将需要执行的任务评审为 reviewed。

可以说“用 plan-executor 执行 docs/todo.json”，或“执行 T03、T04”。

executor 会：

1. 读取 todo.json 和计划文件名；
2. 校验任务路径、状态和依赖；
3. 只读取当前就绪或用户指定任务的完整 plan；
4. 按 DAG 并发派发 subagent；
5. 由主会话统一通过 CLI 回写 todo.json 状态，并按需生成 todo.md 和报告。

长任务还要为每个任务创建独立 run state，默认使用 `worker` 模式，按执行画像等待阶段事件和最终结果；run state 不修改 `docs/todo.json` 或 `docs/todo.md`。详见 `references/runtime-supervision.md` 和 `scripts/run_state.py`。

运行前可用 `python scripts/run_state.py validate --state-dir <external-state-dir>` 检查所有运行记录；`repair` 只修复可推导字段，损坏 JSON 和重复活动 run 必须人工处理。

状态读取、校验、就绪查询和状态写回统一使用 scripts/plan_state.py；脚本输出 JSON，避免模型重复解析 todo.md。

executor 的最小循环是：`ready --state docs/todo.json` → 创建 run state → `claim` → worker 验收 → `complete` 或 `block` → `export-md`。并发写入使用 revision 和状态锁，worker 不直接修改状态文件。

## 默认约定

- 计划目录：docs/plans/
- 状态文件：docs/todo.json
- 可读视图：docs/todo.md，由脚本生成
- 计划命名：T{n}-description-YYYY-MM-DD.md
- 状态枚举：pending、reviewed、in_progress、completed、blocked
- 状态真值：docs/todo.json
- 状态脚本：scripts/plan_state.py

旧 plan 中已有的“状态”字段不删除、不写入，仅用于兼容诊断。
