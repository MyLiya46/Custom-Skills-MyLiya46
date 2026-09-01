---
name: plan-executor
description: 读取 plan-generator 产出的 docs/todo.md 与 docs/plans/，按 todo 的拓扑和状态以并行 subagent 执行已评审任务，跑到验收命令全绿后回写状态，失败隔离为 blocked 并输出报告。
---

# plan-executor · 按需装载 + DAG 并行执行

## 何时使用

当项目已有 docs/todo.md 和 docs/plans/，用户要求“执行 / 实施 / 跑任务 / execute / implement”，或指定任务 ID 时触发。

本 skill 只负责实施：读取任务清单 → 计算就绪集 → 并行派发 → 跑验收命令到绿 → 回写状态 → 汇总报告。

## 对齐契约

- 唯一状态真值：docs/todo.md 的任务总览表。
- plan 文件：执行详情、参数、涉及对象和验收命令；不要求包含生命周期状态。
- 兼容旧格式：旧 plan 中存在“状态”字段时不删除、不写入、不作为调度依据；与 todo 不一致时以 todo 为准并报告。
- 状态枚举：pending、reviewed、in_progress、completed、blocked。
- 默认目录：docs/plans/、docs/todo.md、T{n} 编号，除非用户另行指定。

## 阶段一 · 按需装载与筛选

1. 调用 scripts/plan_state.py validate --todo docs/todo.md；文件不存在或校验失败则停止并报告。
2. 调用 scripts/plan_state.py query --todo docs/todo.md --format json 获取任务映射，再列出 docs/plans/T*.md 文件名；不要读取所有 plan 正文。
3. 调用 scripts/plan_state.py ready --todo docs/todo.md --format json 计算当前就绪集，不手工解析状态或依赖。
4. 按脚本返回的状态处理：
   - pending：不执行，提示回 planner 评审；
   - completed：跳过；
   - blocked：默认不执行，等待用户明确解除条件；
   - reviewed / in_progress：进入候选执行池。
5. 只有任务进入当前就绪集、用户明确指定，或需要诊断其验收命令时，才读取对应 plan 正文。

## 阶段二 · DAG 就绪集并行调度

以 todo 的 blockedBy 和状态为唯一判据：

    ready = {
      task |
      task.status 属于 reviewed、in_progress
      且 task 的所有 blockedBy 状态均为 completed
    }

- 初始就绪集中的任务一次性并发派发，每个任务一个独立 subagent。
- 一个任务完成后立即重算就绪集并派发新解锁任务。
- 一个任务阻塞只影响其后继，不影响其它独立任务。
- 用户指定任务时，仍需经过依赖和状态校验；不越过 blockedBy。

## 阶段三 · 最小上下文执行

每个 subagent 只接收：

1. 本任务 plan 路径和完整正文；
2. plan 实施要点明确点名的涉及文件；
3. 必须按需读取的源方案章节或 todo 中的关键口径；
4. 明确的禁止清单：不得读取整份 PRD、无关 plan 或其它任务的工作区。

执行闭环：

1. 按 plan 的实施要点修改对象，照用已确认的参数；
2. 逐条执行 plan 的验收命令并记录退出码、断言和输出摘要；
3. 失败且能在本任务范围内修复时，留下 diff 记录后重跑；
4. 依赖缺失、方案冲突或参数不成立时停止并上报 blocked，不得扩大任务范围。

subagent 回传至少包含：task_id、status、changed_files、acceptance；阻塞时增加 blocked_reason。

## 阶段四 · 单写者状态回写

主会话统一调用 scripts/plan_state.py 写入 docs/todo.md，避免并行 subagent 同时修改共享文件：

| 触发 | todo 状态 |
|---|---|
| 派发启动 | reviewed → in_progress |
| 全部验收通过 | in_progress → completed |
| 不可行或外部阻塞 | in_progress → blocked |

对应命令：

    python <skill_root>/scripts/plan_state.py set-status --todo docs/todo.md --task T01 T02 --status in_progress --from-status reviewed
    python <skill_root>/scripts/plan_state.py set-status --todo docs/todo.md --task T01 T02 --status completed --from-status in_progress

一次命令可更新同一批次的多个任务；脚本以原子方式写回 todo。

不修改旧 plan 的“状态”字段。若旧 plan 状态与 todo 不一致，在报告中列出，但不因此阻止 todo 已明确的任务执行。

## 边界与异常

- todo 缺少任务、计划路径或依赖关系有环：停止并报告，不执行任何任务。
- plan 文件缺失：只阻塞该任务及其后继，独立任务继续。
- 并行任务修改同一文件且口径冲突：暂停冲突任务，请用户裁决。
- in_progress 重入：重新读取该任务 plan 和验收结果，验证已有副作用后续跑，不重做已确认完成的独立任务。

## 验收标准

- [ ] 启动阶段通过脚本读取 todo 状态和就绪集，不全量读取所有 plan 正文。
- [ ] 就绪集严格依据 todo 的状态和 blockedBy 计算。
- [ ] 可并行任务同时派发，完成后立即解锁后继。
- [ ] 每个 subagent 只读取本任务 plan 和白名单文件。
- [ ] 主会话单写者回写 todo，成功为 completed，失败为 blocked。
- [ ] 中断重入不会重做 completed 任务。
- [ ] 最终报告包含通过、阻塞、跳过、未解锁任务和验收命令结果。

## 输出

报告至少包含：总览、每任务结果表、阻塞原因、因前置阻塞而未启动的任务、待用户决策项。

## 参考

- references/parallel-scheduling.md
- plan-generator 的 plan-template.md、todo-template.md
