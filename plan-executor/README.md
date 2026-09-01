# plan-executor

版本：v1.1.0（beta）

plan-generator 的下游执行器：先读取 docs/todo.md，按状态和 blockedBy 筛选就绪任务，再按需读取对应 plan，用并行 subagent 执行已评审任务，跑到验收命令全绿后回写 completed，失败隔离为 blocked。

## 目录结构

    plan-executor/
    ├── SKILL.md
    ├── README.md
    └── references/
        └── parallel-scheduling.md

## 使用

前置：先在项目目录用 plan-generator 产出 docs/todo.md 和 docs/plans/，并将需要执行的任务评审为 reviewed。

可以说“用 plan-executor 执行 docs/todo.md”，或“执行 T03、T04”。

executor 会：

1. 读取 todo 和计划文件名；
2. 校验任务路径、状态和依赖；
3. 只读取当前就绪或用户指定任务的完整 plan；
4. 按 DAG 并发派发 subagent；
5. 由主会话统一回写 todo 状态并生成报告。

状态读取、校验、就绪查询和状态写回统一使用 scripts/plan_state.py；脚本输出 JSON，避免模型重复解析整份 todo。

## 默认约定

- 计划目录：docs/plans/
- 清单文件：docs/todo.md
- 计划命名：T{n}-description-YYYY-MM-DD.md
- 状态枚举：pending、reviewed、in_progress、completed、blocked
- 状态真值：docs/todo.md
- 状态脚本：scripts/plan_state.py

旧 plan 中已有的“状态”字段不删除、不写入，仅用于兼容诊断。
