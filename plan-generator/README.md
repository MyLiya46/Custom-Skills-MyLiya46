# task-planner

把 PRD / 方案报告 / 技术方案拆解为「逐任务计划文件（plans）+ 拓扑有序任务清单（todo）」，并逐条与用户一对一审阅每份新增计划直到步骤精确到参数、验收可运行——一个通用 skill。

## 目录结构

```text
task-planner/
├── README.md
├── LICENSE
└── task-planner/             # 可分发 / 安装的 skill
    ├── SKILL.md              # skill 核心指令（拆解 + 逐条评审工作流）
    ├── README.md
    └── references/
        ├── plan-template.md  # 计划文件命名规范 + 字段结构 + 写作红线
        └── todo-template.md  # 任务清单三段结构 + 表格字段
```

## 安装

把 `task-planner/` 子目录复制到 Claude 客户端的 skills 目录：

```bash
# Linux / macOS / Windows Git Bash
mkdir -p ~/.claude/skills/task-planner && cp -r task-planner/. ~/.claude/skills/task-planner/
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\task-planner" | Out-Null
Copy-Item -Recurse -Force "task-planner\*" "$env:USERPROFILE\.claude\skills\task-planner\"
```

## 使用

对 Claude 说「用 task-planner 拆解 docs/PRD.md」，或直接 @ 该方案文档并说明诉求——skill 会先拆解为 plans + todos，再逐条与你评审每份新增计划。

支持三种模式：**首次**（目录无 plans/todo，从零建）、**续写**（已有 plans + todo，接着追加，ID 递增、保留既有任务）、**覆盖重拆**（新口径替换旧任务，先确认范围）。

## 默认约定（可换项目覆盖）

- 计划目录：`docs/plans/` (or `docs/tasks/`)，命名 `T{n}-description-YYYY-MM-DD.md`
- 清单文件：`docs/todo.md`（任务总览表 + 拓扑顺序注释 + 交付物核对）

见 [references/](task-planner/references/)。

## License

见 [LICENSE](LICENSE)