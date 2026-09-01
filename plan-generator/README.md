# plan-generator

版本：v1.1.0（beta）

把 PRD、方案报告或技术方案拆解为逐任务计划文件（plans）和拓扑有序任务清单（todo），并按 DAG 批次评审新增计划。

## 目录结构

    plan-generator/
    ├── SKILL.md
    ├── README.md
    └── references/
        ├── plan-template.md
        └── todo-template.md

## 使用

对 Claude Code 或 Codex 说“用 plan-generator 拆解 docs/PRD.md”，或直接引用方案文档并说明诉求。

工作流：

1. 生成或追加 plans 和 todo；
2. 按 DAG 层识别可批量评审的任务；
3. 对跨域、高风险或含未决假设的任务进行深审；
4. 通过后只更新 docs/todo.md 的状态。

同一层且无共享文件或未决口径的任务可以批量评审；数据库迁移、认证、外部服务和跨域契约进入深审。

## 默认约定

- 计划目录：docs/plans/
- 计划命名：T{n}-description-YYYY-MM-DD.md
- 清单文件：docs/todo.md
- 状态真值：docs/todo.md 的任务总览表
- 全局关键口径和跨任务假设：保留在 docs/todo.md 的拓扑与关键口径部分

续写时先读取 todo 和计划文件名，只按影响范围加载历史 plan 正文，不全量扫描无关计划。

状态读取、就绪查询和批量写回使用 scripts/plan_state.py；不要让模型手工扫描 todo 计算状态。

## 与 plan-executor 的衔接

plan-executor 读取 todo 的状态和依赖计算就绪集，只在任务进入执行池后读取对应 plan。旧计划中已有的状态字段保留兼容，但不作为新的调度真值。
