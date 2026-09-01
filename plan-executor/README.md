# plan-executor

**版本**：v1.0.0（beta）

plan-generator 的**下游执行器**：读 `docs/todo.md` + `docs/plans/`，按拓扑顺序用并行 subagent 全自动执行所有 `reviewed` 任务，跑到每份计划的验收命令全绿后回写 `completed`，失败隔离为 `blocked`，最后输出执行报告。

核心指令见 [SKILL.md](SKILL.md)；并行调度与状态流转规则见 [references/parallel-scheduling.md](references/parallel-scheduling.md)。

## 目录结构

```text
plan-executor/
├── SKILL.md                     # skill 核心指令（装载筛选 + 拓扑并行 + 执行到绿 + 回写汇总）
├── README.md
└── references/
    └── parallel-scheduling.md   # 就绪集计算 + subagent 派发契约 + 状态流转 + 失败隔离
```

## 安装

本目录同时支持 Claude Code / Codex（同一份 `SKILL.md`），复制到对应客户端的 skills 目录即可：

**Claude Code**

```bash
# Linux / macOS / Windows Git Bash
mkdir -p ~/.claude/skills/plan-executor && cp -r plan-executor/. ~/.claude/skills/plan-executor/
```

**Codex**

```bash
# Linux / macOS / Windows Git Bash
mkdir -p ~/.codex/skills/plan-executor && cp -r plan-executor/. ~/.codex/skills/plan-executor/
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\plan-executor" | Out-Null
Copy-Item -Recurse -Force "plan-executor\*" "$env:USERPROFILE\.claude\skills\plan-executor\"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.codex\skills\plan-executor" | Out-Null
Copy-Item -Recurse -Force "plan-executor\*" "$env:USERPROFILE\.codex\skills\plan-executor\"
```

## 使用

前置：先在项目目录用 plan-generator 产出 `docs/todo.md` + `docs/plans/`（任务状态须为 `reviewed`）。

对 Claude Code/Codex 说「用 plan-executor 执行 docs/todo.md」，或「实施 / 跑任务」，或指定「执行 T03、T04」。

executor 行为：默认**全自动**——按 `blockedBy` 并发派发、跑到验收命令全绿、回写状态、输出报告；只在失败/阻塞/冲突时停下等你决策。

## 默认约定（可换项目覆盖）

- 计划目录 `docs/plans/`，命名 `T{n}-description-YYYY-MM-DD.md`；清单 `docs/todo.md`。
- 状态枚举五态 `pending | reviewed | in_progress | completed | blocked`（其中`in_progress` 为断点续写标记

## License

MIT，见仓库根目录 [LICENCE](../LICENCE)