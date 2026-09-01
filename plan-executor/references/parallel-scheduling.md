# 并行调度与状态流转规则

本文件是 plan-executor 的可执行规范，SKILL.md 的「阶段二/三/四」据此落地。字段名与状态值严格沿用 plan-generator。

## 1. 输入与事实来源

- 事实来源：`docs/todo.md` 的任务总览表 + `docs/plans/` 下每份计划文件的「状态」字段。
- 二者冲突时以**计划文件「状态」字段**为准，并在汇总里点名该不一致。
- 单次派发的任务 ID 集合 = 用户指定的若干任务（若指定），否则 = 全部 `reviewed | in_progress` 任务，闭包回溯其 `blockedBy` 链。

## 2. 就绪集计算（拓扑分层）

```
输入: 任务映射 {ID -> (blockedBy[], status)}
输出: 就绪集 ready

ready = { t | t.status ∈ {reviewed, in_progress}
             且 ∀ d ∈ t.blockedBy : d.status == completed }

（blockedBy 为空视为 ∀ 条件成立，即无前置 → 就绪）
```

- 初始就绪集算好后，**一次性并发派发**全部就绪任务（每个一个 subagent）。
- 每个 subagent 回传后：
  - `completed` → 立即重算 ready，派发新就绪者；
  - `blocked` → 不回写 completed，故其 `blockedBy` 后继永远不进入 ready（自然隔离）。
- 终止条件：无任务处于 `reviewed/in_progress` 未结算，且 ready 为空。

## 3. subagent 派发契约

### 输入（写进每个 subagent 的任务指令）

1. 本任务的 plan 文件路径（必读，唯一计划真值）。
2. **涉及文件白名单**：本 plan「实施要点」中点名的文件；若 plan 含显式「涉及文件」清单则照用。
3. **禁止清单**（主会话显式写入）：不得读整份 PRD/方案；不得读其它 plan 文件；不得读其它任务正在改动的文件。「关联文档章节」仅按需读被点名的 §X.X，不读全文。
4. 目标：跑到本 plan「验收标准」全部命令通过，或判定不可行并上报。

### 输出（结构化回传，供主会话回写与汇总）

```
{
  task_id: "T01",
  status: "completed" | "blocked",
  changed_files: [ 相对路径... ],
  acceptance: [ { cmd, passed: true|false, note? }... ],
  blocked_reason?: "依赖缺失 X / 方案冲突 Y / 参数 Z 不成立 ..."
}
```

## 4. 状态流转（executor 只写这三个动作）

| 触发 | 计划文件「状态」 | todo「状态」列 | 说明 |
|---|---|---|---|
| 派发启动某任务 | `reviewed → in_progress` | 可暂不动 | `in_progress` = 断点续写标记 |
| subagent 回传 completed | `in_progress → completed` | `completed` | 主会话统一回写 |
| subagent 回传 blocked | `in_progress → blocked` | `blocked` | 附一句阻塞原因到 plan 状态处 |

- executor **只**写 `in_progress / completed / blocked`，绝不写 `pending`，也不把 `reviewed` 降回 `pending`。

## 5. 失败隔离与 blocked 语义

- 「可自修」 vs 「不可行」的判定在 subagent 内：问题能靠本 plan 步骤修复 → 迭代（不设轮次上限，但须在每次迭代留下 diff 记录，禁止原地空转）；问题出在 plan 之外 → 上报 `blocked` + reason。
- 一个任务 `blocked` 不影响其它独立就绪任务继续并行；只影响其 `blockedBy` 直接后继（因前置非 completed 不进入 ready）。
- 两个并行任务改同一文件且口径冲突 → 以 plan 硬约束（单一事实来源 / 零回归）为准；约束不明 → 主会话停下请用户裁决，不静默合并。

## 6. 断点续写（重入判定）

重入即重跑阶段一的分组：

| 发现的状态 | 处置 |
|---|---|
| `completed` | 跳过，不重做 |
| `in_progress` | 断点续写标记：续跑（副作用以 plan 验收命令为准重新验证） |
| `reviewed` | 正常入就绪池 |
| `blocked` | 默认不动；用户明确「解除重试」时按 reviewed 处理 |
| `pending` | 不执行，提示回 planner 评审 |

因为每任务状态已落盘在计划文件，重入无需重放任何记忆，直接读状态即可精确续跑。