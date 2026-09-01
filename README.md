# Custom Skills — MyLiya46

一套可独立安装的 Claude Code/Codex Skill 集合（monorepo）。每个顶层目录都是一个自包含、可直接安装到 `~/.claude/skills/` 或 `<project>/.claude/skills/` 的 skill。

## Skill List

| Skill | 版本 | 性质 | README |
|---|---|---|---|
| [`architecture-printer`](architecture-printer/) | beta | Python 脚本（扫描 + 渲染） | [architecture-printer](architecture-printer/README.md) |
| [`learning-tutor`](learning-tutor/) | beta | 纯提示词（SKILL.md + 领域手册） | [learning-tutor](learning-tutor/README.md) |
| [`plan-generator`](plan-generator/) | beta | 提示词 + 状态脚本（SKILL.md + 模板） | [plan-generator](plan-generator/README.md) |
| [`plan-executor`](plan-executor/) | beta | 提示词 + 状态脚本（plan-generator 下游） | [plan-executor](plan-executor/README.md) |
| [`prompt-polisher`](prompt-polisher/) | beta | 纯提示词（SKILL.md + 模板） | [prompt-polisher](prompt-polisher/README.md) |
| [`repo-committer`](repo-committer/) | beta | Python 脚本（配置 + gitignore 扫描） | [repo-committer](repo-committer/README.md) |
|  |  |  |  |

## 项目结构

每个 skill 遵循同一套目录约定（目录名与 skill `name:` 严格一致）：

```text
<skill-dir>/                  # 可分发 / 安装的 skill 单元（目录名 = frontmatter 的 name）
├── SKILL.md                  # YAML frontmatter（name + description）+ 可执行协议
├── README.md                 # 该 skill 的使用说明
├── references/               # 按需加载的支撑材料（模板 / 清单 / 异常处理表）
└── scripts/                  # 脚本驱动型 skill
```
## 安装

推荐使用仓库自带的安装脚本。默认安装到当前项目，增加 `--global` 则安装到用户级目录；`--skill` 可以重复传入，也可以使用逗号分隔多个 skill，或者使用 `all` 安装全部 skill：

```bash
# 安装全部 skill 到当前项目的 .claude/skills/
bash install.sh --agent claude-code --skill all

# 全局安装指定 skill 到 ~/.codex/skills/
bash install.sh --agent codex --global --skill plan-generator,plan-executor
```

也可以手动复制单个 skill。每个 skill 目录可同时安装到 Claude Code / Codex 的 skills 目录（两者都用 `SKILL.md` 驱动，同一份目录即可；脚本用到的 `<skill_root>` 会自动解析为各自安装路径）。以 `repo-committer` 为例：


**Claude Code**

```bash
cp -r repo-committer ~/.claude/skills/repo-committer
```

**Codex**

```bash
cp -r repo-committer ~/.codex/skills/repo-committer
```

## 收藏常用第三方 Skill 清单

| Skill | 来源 / 说明 | 链接 |
|---|---|---|
| _（示例）skill-name_ | _它解决什么、为何好用_ | _repo / 作者链接_ |
| archify | Agent skill for beautiful, verifiable architecture, workflow, sequence, data-flow, and lifecycle diagrams—self-contained HTML with motion and crisp export. | [archify](https://github.com/tt-a1i/archify) |
|  |  |  |

## License

MIT © 2026 MyLiya46，见 [LICENCE](LICENCE)
