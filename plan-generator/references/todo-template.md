# Todo 状态模板与格式规范

docs/todo.json 是任务生命周期、依赖关系和计划路径的唯一可写真值。docs/todo.md 只是由脚本导出的紧凑视图，不是手工维护的第二份状态。

## JSON 状态模板

    {
      "schema_version": 1,
      "revision": 0,
      "updated_at": "YYYY-MM-DDTHH:MM:SSZ",
      "tasks": [
        {
          "id": "T01",
          "title": "初始化 project-scaffold",
          "blockedBy": [],
          "plan": "plans/T01-project-scaffold-YYYY-MM-DD.md",
          "status": "pending"
        }
      ],
      "markdown_view": {
        "prefix": "...",
        "suffix": "..."
      }
    }

只允许 scripts/plan_state.py 写入 JSON；revision 用于乐观并发保护，状态变更使用 review、claim、complete、block、resume 命令。

## 元信息

    # （项目名）· 实施任务清单

    > 日期：YYYY-MM-DD
    > 计划文件目录：docs/plans/
    > 说明：任务总览按拓扑顺序排列；状态以 docs/todo.json 为准；本文件由脚本生成。

## 一、任务总览

    | 序号 | 任务 ID | 标题（英文短名） | blockedBy | 状态 | 验收要点（摘要） | 计划文件 |
    |---|---|---|---|---|---|---|
    | 1 | T01 | 初始化（project-scaffold） | 无 | pending | 摘要，含可运行验证点 | plans/T01-project-scaffold-YYYY-MM-DD.md |

Markdown 视图字段约定：

- 任务 ID、英文短名、计划文件名三者一一对应。
- blockedBy 必须与拓扑说明一致，整体无环。
- 状态只能使用 pending、reviewed、in_progress、completed、blocked，实际值来自 docs/todo.json。
- reviewed 表示已通过 planner 评审；in_progress、completed、blocked 由 executor 或用户流程写入。
- 计划文件中的旧“状态”字段不作为调度真值；如与 JSON 冲突，以 JSON 为准并报告诊断。

## 二、拓扑与评审批次

只记录层、依赖和批次，不复制全局架构说明：

    - L0 / B01（可批量评审）：T01、T02 无前置。
    - L1 / B02（深审）：T03 依赖 T01。
    - L2 / B03（可批量评审）：T04、T06、T07、T08、T11、T14 依赖 T03。
    - L2 / B04（深审）：T05 依赖 T03、T06。
    - 跨任务共享的关键口径和【假设】保留在本节，不拆到额外文件。

批次规则：同层且无共享文件或未决口径的任务可批量评审；数据库迁移、认证、外部服务、跨域契约和含未决假设的任务进入深审。

## 三、交付物核对

    - [ ] docs/plans/ 下生成计划文件，文件名唯一且与任务 ID 对应
    - [ ] 任一计划可独立开工，包含依赖、步骤和可运行验收项
    - [ ] todo.json 通过 plan_state.py validate，todo.md 已由 export-md 生成
    - [ ] 每任务至少含一条可运行验收命令
    - [ ] 本次新增任务已按批次或深审完成评审

迁移命令：

    python scripts/plan_state.py import-md --todo docs/todo.md --state docs/todo.json
    python scripts/plan_state.py export-md --state docs/todo.json --todo docs/todo.md

续写时只追加 JSON 任务和新增拓扑/批次字段，不手工重写历史任务视图。
