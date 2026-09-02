# plan-executor · 运行监督与可恢复重入

本文件定义子代理的运行态监督，不改变 `docs/todo.json` 的生命周期契约。

## 1. 边界

- `docs/todo.json` 仍是任务状态、依赖和计划路径的唯一可写真值；todo.md 只是生成视图。
- run state 只记录一次子代理执行的 `run_id`、阶段、心跳和验收摘要。
- run state 默认放在项目外的临时目录，避免把监督信息变成工作区修改。
- skill 可以约束主代理的等待和重入决策，但不能改变 Codex 平台内部的硬超时或凭空获得平台级进程事件。
- 如果平台没有原生 agent 状态事件，checkpoint 只能作为辅助证据；不能把“有 checkpoint”解释为进程一定仍存活。

## 2. 执行模式

- `worker` 是默认模式：主会话协调，子代理实施；恢复时必须重新读取并沿用原模式。
- `direct` 只在用户明确要求直接执行，或环境没有可用子代理能力时使用；不能因为子代理暂时无回报就静默切换。
- 模式是 run state 的元数据，不是 `todo.json` 的任务状态。

## 2.1 阶段事件

“开始步骤立即写状态、完成步骤立即写状态”在本 workflow 中映射为阶段事件：

- `phase_started`：即将执行阶段动作前写入；
- `phase_progress`：阶段中有重要进展或进入外部等待时写入；
- `phase_completed`：阶段验收通过后立即写入，并设置下一个恢复边界；
- `phase_blocked`：阶段无法继续时写入原因和 `resume_from`。

阶段事件进入 run state 的 `phase_history`，不能写入 plan 的生命周期字段，也不能替代最终验收。

## 2. 执行画像

执行画像决定等待窗口，不决定 todo 状态。计划中可以显式提供画像；旧 plan 按验收命令保守推断。

| execution_class | 典型任务 | 默认策略 |
|---|---|---|
| `short` | 静态检查、文档、小型单测 | 短启动窗口；短硬截止时间 |
| `normal` | 普通实现和定向测试 | 中等 idle 窗口；一次提醒 |
| `long-infra` | PG、迁移、服务启动、跨进程同步 | 长 idle 窗口；允许外部等待 |
| `external` | HTTP 模型任务、LLM、异步任务 | 依据外部任务上限设置硬截止时间 |
| `e2e` | 多服务真实链路和最终验收 | 使用所有启动、重试、清理和验收时间之和 |

画像至少应能回答：

- 子代理首次 ACK 最晚何时出现；
- 多久没有 checkpoint 才值得检查；
- 整个任务的绝对截止时间；
- 哪些命令或服务属于预期的外部等待；
- 失败后可以从哪个阶段继续。

建议的初始值只是安全默认值，不能替代历史 P95：

| 类型 | startup_timeout | idle_timeout | hard_timeout |
|---|---:|---:|---:|
| `short` | 60 秒 | 5 分钟 | 15 分钟 |
| `normal` | 120 秒 | 10 分钟 | 30 分钟 |
| `long-infra` | 120 秒 | 15 分钟 | 90 分钟 |
| `external` | 180 秒 | 20 分钟 | 120 分钟 |
| `e2e` | 180 秒 | 20 分钟 | 180 分钟 |

`idle_timeout` 是无进度证据的观察窗口，不是“没有最终回复”的窗口。若工具明确报告命令仍在运行，应继续等待，不进入失活判定。

## 3. 运行态

运行态建议使用以下状态：

```text
created -> acknowledged -> running -> waiting_external -> validating
                                      \-> stale_candidate -> reconciling -> running
validating -> completed | blocked | failed
```

这些状态不写入 `docs/todo.json`。`stale_candidate` 表示需要调查，不表示已经失败。

每个 run state 至少包含：

```text
schema_version
task_id
run_id
attempt
mode
profile
status
phase
started_at
updated_at
last_progress_at
current_command
changed_files
checkpoints
phase_history
result_path
acceptance
blocked_reason
resume_from
```

checkpoint 应在“开始实现、完成主要对象、进入验收、外部等待开始/结束、验收项通过”这些阶段边界产生，不要求每条命令或每秒产生一条消息。

## 4. 主代理监督流程

### 派发

1. 读取 plan 的执行画像；没有画像则保守推断。
2. 检查同一任务是否已有活动 run；有则进入协调，不创建第二个 run。
3. 创建唯一 `run_id`，启动子代理并要求先回传 ACK。
4. 只在派发和终态时读取完整任务信息；等待期间不重复读取整个 `todo.json` 或 plan。

### 等待

1. 优先等待子代理完成事件或工具返回。
2. 子代理正在执行命令、等待外部服务或有近期 checkpoint 时，继续等待。
3. 超过 `idle_timeout` 后最多发送一次提醒，要求写入 checkpoint 或最终结果。
4. 提醒后进入 grace period；不要连续发送提醒消息。

### 协调

在关闭或重入前依次检查：

1. 平台是否报告子代理仍在执行；
2. run state 的最后阶段和 `last_progress_at`；
3. 当前命令、子进程和日志是否仍有活动；
4. 工作区是否已经产生部分实现或验收结果；
5. 是否存在同一个 `run_id` 的未回传结果。

仅当确认旧运行已经退出，才允许重入。不能用“工作区没有新文件”替代上述检查。

## 5. 子代理回传契约

子代理启动后先回传：

```text
task_id, run_id, status=acknowledged, profile
```

阶段更新至少包含：

```text
task_id, run_id, status, phase, current_command, changed_files
```

阶段更新还应包含 `event` 和 `phase_history` 中新增的事件；主代理应在收到阶段事件后再决定是否需要读取验收结果，不要用重复询问代替事件。

最终结果至少包含：

```text
task_id
run_id
status
changed_files
checkpoints
acceptance: command, exit_code, assertion/output summary
```

阻塞或失败时追加：

```text
blocked_reason
resume_from
```

worker 派发消息应保持最小且结构化：

```text
task_id, run_id, mode, profile
goal
plan_path and assigned steps
allowed_files
acceptance_commands and success criteria
forbidden_scope
required events and final result schema
```

不要把整份 PRD、无关 plan 或其它任务的工作区内容塞进 worker 上下文；不要让 worker 直接写 `docs/todo.json` 或 `docs/todo.md`。

验收命令内部的 `curl`、健康检查和异步任务轮询应由子代理或脚本完成；主代理只等待子代理的阶段或最终结果。

## 6. 重入规则

- `completed` 任务不重做。
- `in_progress` 任务重入前先核对已有 checkpoint、changed_files 和已通过的验收项。
- 重入沿用原 `run_id`，增加 `attempt`，从 `resume_from` 或最后一个未通过验收项继续。
- 若依赖或参数确实不可行，主代理才通过 `plan_state.py` 将任务置为 `blocked`。
- 运行态暂时不明时保持协调态，不把不确定性直接写成 `blocked`，也不立即重新派发。

## 7. LCT 任务映射

- T03：`long-infra`；checkpoint 可放在模型文件、迁移、`pg_sync` 和验收四个阶段。
- T04：`long-infra`；模型健康检查和 PG 同步属于预期外部等待。
- T06：`external`；模型 `/predict` 和 `/tasks/{id}` 的轮询应留在子代理内部。
- T15：`e2e`；60/120/180 秒是命令或外部链路的局部上限，不能直接作为整个子代理的上限。

## 8. 监督指标

每次执行至少记录：

- ACK 延迟；
- checkpoint 数量和间隔；
- 主代理提醒次数；
- 关闭原因；
- 重入次数；
- 从重入到完成的验收结果。

重点观察“子代理仍有活动但被关闭”的次数和“每个任务的主代理轮询回合数”。前者应为零，后者应接近阶段数，而不是接近命令轮询次数。

## 9. run_state.py 用法

`run_state.py` 只写入指定的外部状态目录，不写 `docs/todo.json` 或 `docs/todo.md`，也不会杀死或重启任何进程。

恢复前先执行：

```text
python <skill_root>/scripts/run_state.py validate --state-dir <external-state-dir>
```

该校验会报告损坏 JSON、同一任务的多个活动 run、非法 `resume_from`、checkpoint 序号/事件和终态字段不一致。可推导的缺失字段使用：

```text
python <skill_root>/scripts/run_state.py repair --state-dir <external-state-dir>
```

`repair` 使用原子写入；无法解析的 JSON、重复活动 run 和无法安全推导的字段不会被覆盖，必须先人工协调。

创建 run：

```text
python <skill_root>/scripts/run_state.py init \
  --state-dir <external-state-dir> \
  --task T03 \
  --mode worker \
  --profile long-infra \
  --external-wait PostgreSQL \
  --checkpoint-phase migration
```

子代理 ACK 或更新阶段：

```text
python <skill_root>/scripts/run_state.py checkpoint \
  --state-file <state-file-from-init> \
  --status waiting_external \
  --event phase_progress \
  --phase migration \
  --current-command "uv run alembic upgrade head"
```

主代理观察运行态：

```text
python <skill_root>/scripts/run_state.py inspect \
  --state-file <state-file> \
  --idle-timeout 900
```

`inspect` 只返回 `idle_seconds`、活动命令、可选 PID 和 `idle_exceeded`，不会自动标记失败或关闭 run。

阶段开始和完成可以使用明确的事件命令：

```text
python <skill_root>/scripts/run_state.py phase-start \
  --state-file <state-file> \
  --phase migration \
  --current-command "uv run alembic upgrade head"

python <skill_root>/scripts/run_state.py phase-complete \
  --state-file <state-file> \
  --phase migration \
  --message "migration completed" \
  --next-checkpoint acceptance
```

协调和恢复：

```text
python <skill_root>/scripts/run_state.py reconcile \
  --state-file <state-file> \
  --reason "等待窗口已到，先检查进程、日志和已有改动"

python <skill_root>/scripts/run_state.py resume \
  --state-file <state-file> \
  --phase acceptance
```

最终回传：

```text
python <skill_root>/scripts/run_state.py finish \
  --state-file <state-file> \
  --status completed \
  --acceptance-json '{"command":"pytest","exit_code":0}'
```
