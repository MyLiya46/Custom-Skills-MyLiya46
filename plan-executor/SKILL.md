---
name: plan-executor
description: 读取 plan-generator 产出的 docs/todo.json 与 docs/plans/，按 JSON 拓扑、状态和任务执行画像以并行 subagent 执行已评审任务，按运行监督契约等待验收完成后回写状态，失败隔离为 blocked 并输出报告。
---

# plan-executor · 按需装载 + DAG 并行执行

## 何时使用

当项目已有 docs/todo.json 和 docs/plans/，用户要求“执行 / 实施 / 跑任务 / execute / implement”，或指定任务 ID 时触发。

本 skill 只负责实施：读取任务清单 → 计算就绪集 → 并行派发 → 跑验收命令到绿 → 回写状态 → 汇总报告。

## 对齐契约

- 唯一可写状态真值：docs/todo.json；docs/todo.md 只是自动生成的可读视图。
- plan 文件：执行详情、参数、涉及对象和验收命令；不要求包含生命周期状态。
- 兼容旧格式：旧 plan 中存在“状态”字段时不删除、不写入、不作为调度依据；与 todo 不一致时以 todo 为准并报告。
- 状态枚举：pending、reviewed、in_progress、completed、blocked。
- 默认目录：docs/plans/、docs/todo.json、T{n} 编号，除非用户另行指定。

## 运行监督契约

- `docs/todo.json` 只记录真实的任务生命周期；`docs/todo.md` 不直接写入；子代理的运行态必须记录在独立的 run state 中，不把 `in_progress` 当作进程存活证明。
- 任务优先读取计划中的“执行画像”；旧 plan 没有画像时，根据验收命令和外部依赖保守推断，涉及 PG、服务启动、HTTP 外部任务或 E2E 的任务不得使用短等待画像。
- “没有最终回复”或“工作区没有新文件”都不是失活证据。活动命令、近期 checkpoint、外部等待声明或仍存活的进程都应继续等待。
- 主代理每个阶段最多提醒一次；提醒后先进入 grace period，再检查运行记录、进程、日志和已有副作用，禁止无证据关闭后立即重复派发。
- 重入必须复用原 `run_id` 和已有 checkpoint，从最后一个未通过的验收项继续；旧运行未确认终止前不得为同一任务创建第二个活动 run。
- 运行记录默认写入项目外的临时状态目录，不修改 `docs/todo.json` 或 `docs/todo.md`；运行记录工具见 `scripts/run_state.py`，监督细则见 `references/runtime-supervision.md`。

## 执行模式契约

- 默认模式是 `worker`：主会话只负责读取、派发、监督、验收汇总和通过 CLI 单写者回写 `todo.json`，实际实现由子代理完成。
- `direct` 只能在用户明确要求直接执行，或当前环境没有可用子代理能力时使用；恢复时必须沿用原 run 的模式，不得静默切换。
- 阶段开始前立即写入 `phase_started`，阶段完成或阻塞后立即写入 `phase_completed` / `phase_blocked`；不得把多个阶段的状态积累到最后一次性回写。

## 阶段一 · 按需装载与筛选

1. 调用 scripts/plan_state.py validate --state docs/todo.json；该命令同时校验被引用 plan 的固定章节和可执行验收命令，文件不存在或校验失败则停止并报告。
2. 调用 scripts/plan_state.py query --state docs/todo.json --format json 获取任务映射，再列出 docs/plans/T*.md 文件名；不要读取所有 plan 正文。
3. 调用 scripts/plan_state.py ready --state docs/todo.json --format json 计算当前就绪集，不手工解析状态或依赖。
4. 按脚本返回的状态处理：
   - pending：不执行，提示回 planner 评审；
   - completed：跳过；
   - blocked：默认不执行，等待用户明确解除条件；
   - reviewed / in_progress：进入候选执行池。
5. 只有任务进入当前就绪集、用户明确指定，或需要诊断其验收命令时，才读取对应 plan 正文。
6. 从 plan 提取执行画像：`execution_class`、外部等待、预期阶段和恢复边界；缺失时按 `references/runtime-supervision.md` 的保守规则推断。
7. 确定执行模式；未显式指定时使用 `worker`，并在创建 run state 时固定记录。

## 阶段二 · DAG 就绪集并行调度

以 todo.json 的 blockedBy 和状态为唯一判据：

    ready = {
      task |
      task.status 属于 reviewed、in_progress
      且 task 的所有 blockedBy 状态均为 completed
    }

- 初始就绪集中的任务一次性并发派发，每个任务一个独立 subagent。
- 一个任务完成后立即重算就绪集并派发新解锁任务。
- 一个任务阻塞只影响其后继，不影响其它独立任务。
- 用户指定任务时，仍需经过依赖和状态校验；不越过 blockedBy。
- 派发前为每个任务创建一个唯一 `run_id`；发现同一任务已有活动 run 时，先走重入/协调流程，不重复创建。
- 新 run 的顺序固定为：创建 run state → 将 `reviewed` 任务置为 `in_progress` → 派发 worker；已有 `in_progress` 重入只复用原 run，不重复写启动状态。

## 阶段三 · 最小上下文执行

每个 subagent 只接收：

1. 本任务 plan 路径和完整正文；
2. plan 实施要点明确点名的涉及文件；
3. 必须按需读取的源方案章节或 todo.json 中的关键口径；
4. `task_id`、`run_id`、执行模式和执行画像；
5. 明确的成功标准、验收命令和回传格式；
6. 明确的禁止清单：不得读取整份 PRD、无关 plan 或其它任务的工作区。

执行闭环：

1. 启动后先回传 `acknowledged`，再按阶段回传 checkpoint；长命令期间不要求每秒自然语言回复；
2. 每个阶段开始前写 `phase_started`，按 plan 的实施要点修改对象，照用已确认的参数；
3. 每个阶段完成后写 `phase_completed`；逐条执行 plan 的验收命令并记录退出码、断言和输出摘要；命令内部的 HTTP/服务轮询由子代理或其脚本完成，不升级为主代理轮询；
4. 失败且能在本任务范围内修复时，留下 diff 记录后重跑；无法继续时写 `phase_blocked` 并保留 `resume_from`；
5. 依赖缺失、方案冲突或参数不成立时停止并上报 blocked，不得扩大任务范围。

监督规则：

1. 主代理优先等待子代理完成事件或工具结果，不反复发送“仍在执行吗”的消息。
2. 只有超过画像的 `idle_timeout` 且没有活动命令、checkpoint 或外部等待证据时，才标记为 `stale_candidate`。
3. `stale_candidate` 只能触发一次提醒和一次协调检查；协调前不得关闭或重派。
4. 确认子代理已经退出后，才可用同一 `run_id` 重入；确认外部阻塞且本任务无法处理时，才回写 `blocked`。

subagent 回传至少包含：task_id、run_id、mode、status、phase、phase_history、changed_files、checkpoints、acceptance；阻塞时增加 blocked_reason 和 resume_from。

## 阶段四 · 单写者状态回写

主会话统一调用 scripts/plan_state.py 写入 docs/todo.json，避免并行 subagent 同时修改共享文件：

| 触发 | todo 状态 |
|---|---|
| 派发启动 | reviewed → in_progress |
| 全部验收通过 | in_progress → completed |
| 不可行或外部阻塞 | in_progress → blocked |

对应命令：

    python <skill_root>/scripts/plan_state.py claim --state docs/todo.json --task T01 T02 --if-revision 12
    python <skill_root>/scripts/plan_state.py complete --state docs/todo.json --task T01 T02 --acceptance-note "all acceptance commands passed"

一次命令可更新同一批次的多个任务；脚本使用 revision、锁文件和原子替换写回 todo.json，需要展示时再调用 export-md。

不修改旧 plan 的“状态”字段。若旧 plan 状态与 todo 不一致，在报告中列出，但不因此阻止 todo 已明确的任务执行。

## 边界与异常

- todo 缺少任务、计划路径或依赖关系有环：停止并报告，不执行任何任务。
- plan 文件缺失：只阻塞该任务及其后继，独立任务继续。
- 并行任务修改同一文件且口径冲突：暂停冲突任务，请用户裁决。
- in_progress 重入：重新读取该任务 plan 和验收结果，验证已有副作用后续跑，不重做已确认完成的独立任务。
- 子代理没有最终回复但有活动命令或最近 checkpoint：继续等待，不得按超时失败处理。
- 运行记录缺失、run_id 冲突或旧进程是否终止无法确认：进入协调状态，暂停同一任务的重复派发。

## 验收标准

- [ ] 启动阶段通过脚本读取 todo 状态和就绪集，不全量读取所有 plan 正文。
- [ ] 启动阶段通过 `run_state.py validate` 检查损坏 JSON、重复活动 run、恢复边界和终态一致性。
- [ ] 就绪集严格依据 todo.json 的状态和 blockedBy 计算。
- [ ] 可并行任务同时派发，完成后立即解锁后继。
- [ ] 每个 subagent 只读取本任务 plan 和白名单文件。
- [ ] 每个 subagent 有执行画像、唯一 run_id、阶段 checkpoint 和可恢复的最终回传。
- [ ] 主会话不会以无文件变化或单次无回复直接关闭子代理；重派前完成协调检查。
- [ ] 主会话单写者回写 todo，成功为 completed，失败为 blocked。
- [ ] 中断重入不会重做 completed 任务。
- [ ] 最终报告包含通过、阻塞、跳过、未解锁任务和验收命令结果。

## 输出

报告至少包含：总览、每任务结果表、阻塞原因、因前置阻塞而未启动的任务、待用户决策项。

## 参考

- references/parallel-scheduling.md
- references/runtime-supervision.md
- scripts/run_state.py
- plan-generator 的 plan-template.md、todo-template.md
