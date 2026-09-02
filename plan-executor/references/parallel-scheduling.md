# 并行调度与状态流转规则

本文件是 plan-executor 的可执行补充规范。任务状态、依赖和计划路径均来自 docs/todo.json；todo.md 只作为自动生成视图。

## 1. 输入与事实来源

- 通过 scripts/plan_state.py query 和 ready 获取 ID、blockedBy、status、planPath；默认参数为 `--state docs/todo.json`。
- 列出 docs/plans/T*.md 文件名，验证路径存在；不为建立映射而读取全部正文。
- 旧 plan 中的“状态”字段仅作兼容诊断，不参与调度。

## 2. 就绪集

    ready = {
      t | t.status 属于 reviewed、in_progress
          且 t.blockedBy 为空或所有前置状态均为 completed
    }

- 初始 ready 集合一次性并发派发。
- subagent 回传 completed 后立即重算并派发新解锁任务。
- subagent 回传 blocked 时不解锁后继，但其它独立任务继续。
- pending、completed、blocked（未明确解除）不进入 ready。

## 3. 按需读取与派发契约

派发前才读取该任务的完整 plan。每个 subagent 的输入包括：

1. 本任务 plan 路径和正文；
2. 实施要点点名的文件白名单；
3. 必要的源方案章节或决策 ID；
4. 禁止读取整份 PRD、无关 plan 和其它任务工作区的说明。

输出至少包含 task_id、status、changed_files、acceptance；阻塞时增加 blocked_reason。

派发前还要创建独立的 run state：

- 一个任务同一时间只允许一个活动 `run_id`；
- `docs/todo.json` 仍由主会话单写者维护，todo.md 由 `export-md` 生成，run state 不替代任务生命周期；
- 画像和 checkpoint 规则见 `runtime-supervision.md`；
- 旧 plan 没有执行画像时，遇到 PG、服务启动、外部 HTTP 任务或 E2E 采用保守的长任务画像。
- 默认以 `worker` 模式派发；若使用 `direct`，必须有用户明确授权并在恢复时保持不变。
- 阶段开始、完成和阻塞要即时写入 run state，不能等整个任务完成后批量补写。

派发顺序固定为：创建 run state → `reviewed` 改为 `in_progress` → 派发 worker。`in_progress` 重入只协调和复用原 run，不重复创建 worker。

worker payload 至少包含：

1. `task_id`、`run_id`、执行模式和执行画像；
2. 当前 plan 路径及完整正文；
3. 涉及文件白名单和禁止读取范围；
4. 实施目标、验收命令、成功标准；
5. ACK、阶段事件和最终结果的结构化回传要求。

## 4. 状态回写

主会话是唯一写者，只通过 scripts/plan_state.py 更新 docs/todo.json：

| 触发 | todo 状态 |
|---|---|
| 派发启动 | reviewed → in_progress |
| 全部验收通过 | in_progress → completed |
| 不可行或外部阻塞 | in_progress → blocked |

旧 plan 的“状态”字段不删除、不写入、不作为真值。

批量状态写入示例：

    python <skill_root>/scripts/plan_state.py complete --state docs/todo.json --task T01 T02 --acceptance-note "all acceptance commands passed"

## 5. 失败隔离与重入

- 能在本任务范围内修复的问题，记录 diff 后继续迭代验收。
- 依赖缺失、方案冲突或参数不成立时上报 blocked，只影响后继任务。
- in_progress 重入时重新读取该 plan，先验证已有副作用，再从未通过的验收项继续。
- 两个并行任务产生冲突时暂停冲突任务，请用户裁决，不静默合并。

## 6. 等待与失活判定

- 子代理启动后先回传 ACK，阶段变化时写 checkpoint；没有新文件不影响存活判断。
- 主会话优先等待完成事件或工具结果，不按固定短周期向子代理发送状态询问。
- 只有超过 `idle_timeout` 且无活动命令、checkpoint、外部等待证据时，才进入 `stale_candidate`。
- `stale_candidate` 先提醒一次并进入 grace period；随后必须检查运行记录、进程、日志和工作区副作用，确认退出后才允许重入。
- 重入沿用原 `run_id` 和最后 checkpoint；不得在旧进程未确认结束时创建第二个同任务代理。

具体画像、状态字段和 `run_state.py` 命令见 `runtime-supervision.md`。
