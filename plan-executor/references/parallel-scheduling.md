# 并行调度与状态流转规则

本文件是 plan-executor 的可执行补充规范。任务状态、依赖和计划路径均来自 docs/todo.md。

## 1. 输入与事实来源

- 通过 scripts/plan_state.py query 和 ready 获取 ID、blockedBy、status、planPath。
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

## 4. 状态回写

主会话是唯一写者，只通过 scripts/plan_state.py 更新 docs/todo.md：

| 触发 | todo 状态 |
|---|---|
| 派发启动 | reviewed → in_progress |
| 全部验收通过 | in_progress → completed |
| 不可行或外部阻塞 | in_progress → blocked |

旧 plan 的“状态”字段不删除、不写入、不作为真值。

批量状态写入示例：

    python <skill_root>/scripts/plan_state.py set-status --todo docs/todo.md --task T01 T02 --status completed --from-status in_progress

## 5. 失败隔离与重入

- 能在本任务范围内修复的问题，记录 diff 后继续迭代验收。
- 依赖缺失、方案冲突或参数不成立时上报 blocked，只影响后继任务。
- in_progress 重入时重新读取该 plan，先验证已有副作用，再从未通过的验收项继续。
- 两个并行任务产生冲突时暂停冲突任务，请用户裁决，不静默合并。
