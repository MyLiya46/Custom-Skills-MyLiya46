# learning-tutor

**版本**：v1.0.0（beta）

一对一渐进式 AI 教师：把任意学习主题拆成「诊断 → 计划 → 逐课教学 → 进度更新 → 动态调整」5 步流程，一次只交付一课，每课含讲解、问题清单与费曼输出练习。覆盖计算机、金融、生活三类领域，未知领域走通用流程。

核心指令见 [SKILL.md](SKILL.md)；领域教学手册见 [references/domains/](references/domains/)。

## 目录结构

```text
learning-tutor/               # 本目录（可分发 / 安装的 skill）
├── SKILL.md                # skill 核心指令（触发路由 + 5 步教学流程 + 输出模板 + 检查清单）
├── README.md               # 本文件
└── references/
    └── domains/
        ├── cs.md           # 计算机领域教学手册（MCP / C++ / Python）
        ├── finance.md      # 金融领域教学手册（理财 / 股票 / 基金 / 财报）
        └── life.md         # 生活领域教学手册（做饭 / 健身 / 跑步）
```

## 安装

把本目录复制到 Claude 客户端的 skills 目录：

```bash
# Linux / macOS / Windows Git Bash
mkdir -p ~/.claude/skills/learning-tutor && cp -r . ~/.claude/skills/learning-tutor/
```

```powershell
# Windows PowerShell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\learning-tutor" | Out-Null
Copy-Item -Recurse -Force * "$env:USERPROFILE\.claude\skills\learning-tutor\"
```

## 使用

直接提出学习主题即触发，例如：

- 「我想系统学习一下 MCP」
- 「我想学习一下 C++ 快速上手」
- 「我想学理财」「想学做饭」

skill 会先用 AskUserQuestion 做 2 轮诊断（水平 / 时间 / 目标 / 期限等），协商学习计划并生成 `<学习主题>-学习计划.md`，再逐课教学。

## 默认约定（可协商）

- 每课容量默认 25 分钟可消化量，可协商调整。
- 每课至少 1 个费曼输出任务（复述 / 类比 / 教给他人 / 动手练习等）。
- 计划文档默认持久化到当前目录 cwd，也可选 `.learning-tutor/` 子目录或仅会话内。
- 授课全程中文；时间以分钟计、数量以个数计。

见 [SKILL.md](SKILL.md)。

## License

MIT，见仓库根目录 [LICENCE](../LICENCE)。