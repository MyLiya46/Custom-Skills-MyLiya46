---
name: plan-executor
description: 读取 plan-generator 产出的 docs/todo.md 与 docs/plans/，按拓扑顺序以并行 subagent 全自动执行所有 reviewed 任务，跑到每份计划的验收命令全绿后把状态回写为 completed，失败则隔离为 blocked 并继续其它独立任务，最后输出执行报告。When the user has plans + todo from plan-generator and says "执行 / 实施 / 跑任务 / execute / implement", run all reviewed tasks in topological order via parallel subagents until every acceptance command passes, write status back (completed/blocked), and summarize.
---

# plan-executor · 计划执行器（plan-generator 的下游）

## 何时使用

当用户已用 **plan-generator**（或其等价物）产出了 `docs/todo.md` + `docs/plans/` 下的计划文件，并对 Claude Code/Codex 说「**执行**」「**实施**」「**跑任务**」「全部跑完」，或直接指定其中某几个任务 ID 时触发本 skill。

本 skill 只做**实施**。它把「从计划到代码落地」这一过程固化：读计划 → 按拓扑并行派发 → 跑到验收命令全绿 → 回写状态 → 汇总报告。


## 对齐契约（严格沿用 plan-generator）

- **状态枚举** ：`pending`（待评审）/ `reviewed`（已评审可开工）/ `in_progress`（执行中，用于**断点续写**——中断后据此精确续跑）/ `completed`（已完成）/ `blocked`（阻塞）。executor 只写 `in_progress` / `completed` / `blocked`，不再新增其它状态值。
- **计划文件**：`docs/plans/T{n}-description-YYYY-MM-DD.md`，字段为 `任务 ID / 标题与目标 / 关联文档章节 / 前置依赖 blockedBy / 实施要点 / 验收标准 / 状态`。
- **任务清单**：`docs/todo.md`，元信息 + 「任务总览表」（含 `blockedBy`、`状态` 列）+ 拓扑顺序注释 + 交付物核对。
- **blockedBy**：`无` 或逗号分隔的任务 ID 列表，与 todo 表、拓扑注释严格一致，整体无环 DAG。
- **默认目录**：`docs/plans/`、`docs/todo.md`、`T{n}` 编号——除非用户另行指定项目约定，否则不改。

## 执行流程（阶段化）

### 阶段一 · 装载与筛选

1. 读 `docs/todo.md`。不存在 → 终止，提示「先跑 plan-generator 产出 plans + todo」。
2. 读 `docs/plans/` 下全部 `T*.md`，建立「任务 ID → 计划文件」映射。
3. 校验一致性：todo 表每个任务都有对应 plan 文件；`blockedBy` 无环、与拓扑注释一致。不一致 → 停在原地报告
4. 按「状态」把任务分四组，明确各自的处置：
   - `pending` → **不执行**；汇总时提示用户「先回 planner 完成评审」，绝不把 pending 当 reviewed。
   - `completed` → **跳过**，不重做（断点续写的核心：已完成即不重做）。
   - `blocked` → 默认**不自动执行**；仅当用户本次明确要求「解除并重试」时，先确认解除条件，再按 `reviewed` 处理。
   - `reviewed` / `in_progress` → 进「待执行池」（`in_progress` = 断点续写标记，视为上次未跑完，续跑）。

### 阶段二 · 拓扑分组（并行调度）

以 `blockedBy` 为**唯一并发判据**，计算「就绪集」：

- **就绪集 = 状态为 reviewed/in_progress，且其 blockedBy 为空，或 blockedBy 中每个前置任务状态均为 `completed` 的任务。**
- 对当前就绪集，**每个任务各起一个独立 subagent，一次性并发启动**，互不等待、绝不「跑完一个再跑下一个」。
- 每当一个 subagent 回传成功（`completed`），**立即重算就绪集**，把刚被解锁的任务马上派发；被阻塞任务静默等待，绝不越过 blockedBy 提前启动。
- 用户只指定了「某几个任务」时：仍按 blockedBy 判据，从这几个人里筛出就绪者并发执行，其余不启动。

精确算法见 [references/parallel-scheduling.md](references/parallel-scheduling.md)。

### 阶段三 · 执行到绿（subagent 内）

每个 subagent 只拿到**最小上下文**，执行三步闭环：

1. **落地**：按该 plan 的「实施要点」逐条改动代码/文件，步骤落到「对象 + 动作 + 参数值」，照抄 plan 中已定参数，不擅自改值。
2. **跑验收**：逐条执行 plan「验收标准」里的可运行命令（`pytest …` / `npm run build` / `curl …`），记录每条结果（退出码 / 断言）。“绿” = 该命令退出码 0 / 断言通过。
3. **迭代到绿**：某条件验证不绿 → 在**本任务范围**内（只动本 plan 点名的文件）修复并重跑；循环，直到该计划**全部验收项通过**才收尾。
   - 判断「可自修」还是「不可行」：问题能靠本 plan 步骤内修复 → 继续迭代；问题出在 plan 之外（依赖缺失 / 方案冲突 / 参数不成立）→ 停止迭代，上报 `blocked` + 原因，不硬扛。

subagent 的输入/输出契约见 [references/parallel-scheduling.md](references/parallel-scheduling.md)。

### 阶段四 · 状态回写与汇总

1. subagent 回传结构化结果后，由**主会话**统一回写（单写者，避免多个并行 subagent 同时改共享的 `todo.md` 造成冲突）：
   - 成功 → 该 plan 文件「状态」字段与 `todo.md` 状态列 **`reviewed/in_progress → completed`**。
   - 失败 → 二者 **`reviewed/in_progress → blocked`**，并在 plan 文件「状态」处补一句阻塞原因。
2. 全部可执行任务都落到 `completed` 或 `blocked` 后，主会话输出执行报告（见「输出」）。

## 核心优势（每条即机制，不存在“自动压缩”这类空话）

### 多路并行
- **机制**：无环 DAG 上以 `blockedBy` 做就绪判定；同一就绪集内任务并发起 subagent；一个完成即重算并解锁后继。并发的力度 = 就绪集大小，不人为插队。
- **落点**：阶段二的「就绪集计算 + 一次性并发派发 + 完成即解锁」。

### 上下文压缩
- **机制**：任务级上下文隔离——每个 subagent 的**可读白名单 = 本任务的 plan 文件 + 实施要点里点名的涉及文件**；主会话在派发指令里写明这份白名单，并**显式禁止**读整份 PRD / 方案、其余 plan、其余任务的改动。subagent 跑完即释放上下文。
- **落点**：阶段三派发时把白名单写进 task 指令；“关联文档章节”字段仅按需读该章节，不读全文。

### 断点续写
- **机制**：**唯一事实来源 = 计划文件的「状态」字段**（+ todo 状态列），其中 `in_progress` 是「执行到一半」的断点续写标记。中断后重入，阶段一按状态分组：`completed` 跳过、`in_progress` 据标记续跑、`reviewed` 起跑、`blocked` 待决策、`pending` 回 planner。因为每任务状态都落盘，重入不重做已完成任务。
- **落点**：阶段一第 4 步的分组 + 阶段四的回写形成「进度落盘 → 可重入」。

### 验收闭环
- **机制**：**真值 = plan「验收标准」里的可运行命令**。subagent 逐条执行、逐条记录，全绿 → 回写 `completed`；不绿 → 修复重跑，直到全绿才回写。号口的「绿」就是命令通过，不是“看着差不多了”。
- **落点**：阶段三第 2、3 步 + 阶段四回写。

### 失败隔离
- **机制**：单任务 subagent 失败 → 只把该任务标 `blocked` 并记录原因，不动其它 subagent；blockedBy 它的后继因前置非 `completed` 而自然不被启动，其它独立任务继续并行。冲突或不可行时停在原地记录，不连带、不误伤。
- **落点**：阶段四失败分支 + 「边界与异常处理」。

## 边界与异常处理

- `pending` 任务不执行 → 提示先回 planner 评审，不把 pending 当 reviewed。
- 某计划「本身不可行」（依赖缺失 / 方案冲突 / 参数不成立）→ 标 `blocked`、在 plan 状态处写清原因，跳过并记录，继续其它独立任务，最后统一汇总等用户决策。
- 两个并行任务产出冲突（改同一文件、口径打架）→ 以 plan 内的硬约束（单一事实来源 / 零回归）为准；约束不明时**停下向用户确认**，不擅自合并。
- 计划文件与 todo 表状态不一致 → 以计划文件「状态」字段为准，并在汇总里点名该不一致，请用户裁决。

## 输出（执行报告）

主会话整理一份报告，含：

1. **总览**：本次可执行 N 个，绿 M 个、阻塞 K 个、跳过 J 个（completed）/ P 个（pending）。
2. **每任务结果表**：`任务 ID | 标题 | 结果（通过/阻塞）| 改动文件摘要 | 验收命令条数（绿/总）`。
3. **阻塞任务清单及原因**；**因前置阻塞而无法启动的任务**单独列出。
4. **待用户决策项**：blocked 的解除条件、冲突裁定点、pending 提醒。

## 禁止事项

- 不要把整份 PRD / 方案文档反复塞进每个执行上下文；只加载「本任务 plan + 实施要点点名的涉及文件」。
- 不要串行执行可并行的任务；不要越过 blockedBy 提前启动后继。
- 不要自造字段名、状态值、文件命名——严格对计划文件模板与 todo 模板。
- 不要擅自改 plan 里已定的参数值、表名、事件名、错误码（照抄计划，有冲突才停下确认）。

## 验收标准

- [ ] 所有 `reviewed` 任务按拓扑顺序跑完，每份计划验收命令全绿才回写 `completed`。
- [ ] 并发派发依据 blockedBy 就绪集，可并行任务同时启动，无串行傻等。
- [ ] 每个 subagent 只加载「本任务 plan + 涉及文件」，未读取整份 PRD/方案。
- [ ] 状态回写正确：绿 → `completed`，失败 → `blocked`（含原因），跳过/待决策如实标注。
- [ ] 中断重入时，`completed` 不重做、`reviewed/in_progress` 续跑、`blocked` 待决策。
- [ ] 最终输出执行报告，含通过/阻塞/跳过与阻塞原因清单。

## 交互约定

- 执行前如发现影响「并行边界 / 状态流转 / 冲突判定」的歧义，先确认，不擅自定界。
- 输出报告后欢迎增删改，本轮最多迭代 3 轮。

## 参考

- [references/parallel-scheduling.md](references/parallel-scheduling.md) — 就绪集计算 + 并发派发契约 + 状态流转 + 失败隔离规则
- plan-generator 的模板（对齐契约的出处）：`plan-generator/references/plan-template.md`、`plan-generator/references/todo-template.md`