# repo-committer

**版本**：v1.0.0（beta）

一次性把当前 git 仓库的所有未提交改动，按逻辑主题拆成原子化提交序列（一条 Conventional Commits 规范的 commit message 对应一组逻辑改动），逐组精确提交，提交后询问是否 push。

**默认零询问直达结果**：读得到配置就直接用，只在真正需要决策时才问；问了就写回 committer config，下次免问。

## 安装

把本仓库的 `repo-committer/` 目录（即本目录）安装为你的 Claude Code skill：

```bash
# 在仓库根目录下执行
cp -r repo-committer ~/.claude/skills/repo-committer
```

Windows（PowerShell）等价：

```powershell
Copy-Item -Recurse -Force repo-committer "$env:USERPROFILE\.claude\skills\repo-committer"
```

安装后，在任意 git 仓库内对 Claude Code 说「（用 repo-committer）帮我提交改动」即可触发。

## 术语：git config vs committer config

| 术语 | 是什么 | 由谁管理 |
|------|--------|----------|
| **git config** | 本机/本仓库的 git 原生配置：`user.name`/`user.email`/`remote`/`origin`/`branch`/`.gitignore` 等 | git 管（`git config`、`git remote`） |
| **committer config** | repo-committer 的 `config.json`：记录扫描来的身份，以及**每个项目**的属地设置 | repo-committer 管（`scripts/` 下脚本自读自改） |

committer config 的 `project` 字段按「项目根目录绝对路径」为 key 记忆每个项目的 remote / branch / user / email。

## 触发方式

默认（完整流程）：

- 「帮我提交改动」「提交一下」「commit 这些改动」

带参数：

| 参数 | 含义 | 这么说也能触发 |
|------|------|----------------|
| （无参数） | 完整提交流程 | 「帮我提交改动」「提交一下」「commit 这些改动」 |
| `--show` | 打印 committer config（`read_config.py`） | 「显示我的提交配置」「看看当前配置」 |
| `--show-project` | 打印当前项目条目（`project.py get`） | 「显示这个项目的配置」 |
| `--set <自然语言>` | 调整 committer config（映射为确定化字段后落盘） | 「把我的邮箱改成 x@y.com」「别每次问 push」 |
| `--init` | 扫描本机 git config 生成 committer config | 「初始化提交配置」「扫一下本机 git 身份」 |
| `--gitignore` | 校验并补写当前项目 .gitignore（`project.py gitignore`） | 「校验 .gitignore」「看看有没有敏感文件会被提交」 |

> 这些参数是**自然语言触发语义**，不是可传的命令行参数——skill 无真实 argv，由模型把上面的说法映射到下方脚本执行。等价说法不限于表中示例，把握意图即可。

## 首次准备（推荐先做一步）

```bash
# 在 skill 安装目录（本 README 所在目录）下执行；Python 解释器按环境取 python / python3 / py
# 方式一（推荐）：扫描本机 git 身份 + 当前仓库 remote/branch，生成结构完整的 config.json
python scripts/init_config.py

# 方式二（可选）：仅生成空骨架（结构完整、值留空待定）
python scripts/sync.py
```

> `config.example.json` 仅作字段参考（含 `// 示例 …` 注释键，不会被脚本读入）；`config.json` 由脚本运行时自动生成完整结构，扫不到的字段留空、首次使用按需问询后写回。没有 config.json 时，skill 运行中也会自动 `init_config.py` 扫描本机 git config 生成。**有 config.json 时，skill 一律优先按它执行。**
>
> ⚠️ 不要手工 `cp config.example.json config.json`——模板中的占位假值（假身份 / 假远程）会立即生效且不再扫描真实 git config。

## 进入场景

skill 每次进入项目先解析项目根（`project.py resolve-project`，向上找 `.git`），再判定四场景：

| 场景 | 判定 | 行为 |
|------|------|------|
| **A. 新项目（无 git）** | 无 `.git` | 扫 git global config 取 user/email → 按需询问 → 写 committer config → 登记项目 |
| **B. 有 git、无 repo-committer 记录** | 有 `.git`，但 `project` 无此项目 key | 先扫 git 身份再扫 repo（remote/branch）→ 按需询问 → 写 committer config → 登记项目 |
| **C. 已有记录（缺字段）** | 有此项目 key 但缺某些字段 | 用已有字段 + 按需询问补全 |
| **D. 已有记录（完整）** | `project` 有此项目 key 且字段齐备 | 直接用该项目配置执行，不重复扫描、不询问 |

每次执行都会做**隐私拦截**（`project.py gitignore --dry-run` 只读报告，确认后才写）：只针对「即将 git add 的新改动」判断敏感项，不看已提交内容；已忽略项用 `git check-ignore` 判断（正确处理嵌套 `.gitignore`），共识项、经验敏感项都先列入清单，经用户确认后再补写 `.gitignore`。

## 工作流程

1. **解析项目根**：`project.py resolve-project`。
2. **判定场景**：A/B/C/D，按上表决定扫描 / 直接用缓存 / 补全。
3. **隐私拦截**：`project.py gitignore --dry-run` 只读扫描待 `git add` 的新改动并报到清单；`git check-ignore` 判断已忽略（含嵌套 `.gitignore`）；确认后才写 `.gitignore`。
4. **确保 git 仓库**：非仓库且 `auto_git_init=true` 时自动 `git init`。
5. **扫现状**：`git status --porcelain` + `git diff`；无改动则友好提示结束。
6. **列分组方案**：按逻辑主题归组。
7. **出清单待确认**：分组 + 每组 message + 拟补写 `.gitignore` 项一次性列出。
8. **逐组提交**：精确 `git add` → `git -c user.name=… -c user.email=…` 提交。
9. **问 push**：仅问一次；按 `remotes` 清单多远程推送。

## config.json（committer config）字段说明

```jsonc
{
  // 全局默认身份（项目级 current_user 覆盖它）；空串 = 未知，扫描不到时按需问询
  "current_user": { "name": "", "email": "" },
  // 备选身份
  "alternate_user": [],
  // 全局默认远程清单（项目级 remotes 覆盖它）
  "remotes": [],
  // 全局行为
  "behavior": { "ask_before_push": true, "auto_include_untracked": false, "auto_git_init": true },

  // 提交消息语言："zh"（中文，默认）或 "en"（英文）
  "commit_message_language": "zh",

  // 项目级记忆：按「项目根绝对路径」为 key（由 init_config.py / project.py 运行时登记）
  "project": {
    "/absolute/path/to/your/repo": {
      "current_user": { "name": "…", "email": "…" },
      "remotes": [
        { "name": "origin", "url": "…",
          "branch": "main", "enabled": true }
      ]
    }
  }
}
```

> 完整字段结构及每条 remote 字段的示例见 `config.example.json` 的 `// 示例 …` 注释键。

**优先级**：项目级 `project[<root>]` 的字段 > 全局字段 > 提问补全。

### 多远程推送规则

- 只推 `enabled == true` 的远程；`false` 跳过并标注。
- `url` 缺失或为空 → 跳过并标注「url 为空（疑似未配置/占位）」，不 `git remote add`、不 push。
- 直接 `git push <name> <branch>`，**不读取 / 不注入任何 SSH key**——认证走 git 自身配置（`~/.ssh/config`、credential helper）；认证 / SSH 未配置导致失败时，如实报告原始错误并提示用户检查 SSH 配置，不代改、不重试。
- **同一远程多分支**：允许多条 `name` 相同、`branch` 不同的条目，按顺序逐条 push。
- **branch 缺省**：空 → 当前分支（`git branch --show-current`）；`init_config.py` 生成时 branch 一律留空，仅当显式指定「固定推某分支」才填非空。
- 单远程失败不阻断其余，逐条报告，绝不 `--force`。

### 改配置（自然语言 → 确定化字段）

| 自然语言 | 落盘命令 |
|----------|----------|
| 「把邮箱改成 xxx@example.com」 | `project.py set current_user.email …`（项目维度；说「全局默认」才用 `write_config.py`） |
| 「把提交人改成「姓名」」 | `project.py set current_user.name …` |
| 「停用某个远程」 | `project.py set remotes.<idx>.enabled false` |
| 「删除某个远程/身份」 | `project.py remove <dotted>`（写全局才用 `write_config.py remove`） |
| 「同一远程再加一个分支」 | 追加同名不同 branch 条目 |
| 「不要每次问 push」 | `write_config.py behavior.ask_before_push false` |

## scripts 目录

| 脚本 | 作用 |
|------|------|
| `scripts/_config_util.py` | 共享工具：config.json 定位、读写、dotted 下钻（get/set/remove/load/save） |
| `scripts/read_config.py` | 读 committer config |
| `scripts/write_config.py` | 写全局字段（幂等）；`remove <dotted>` 删除字段/数组元素 |
| `scripts/sync.py` | 从模板生成 config.json 骨架（值留空待定） |
| `scripts/init_config.py` | 扫描本机 git config + 现场 remote/branch，生成完整 committer config |
| `scripts/project.py` | 项目级命令：`resolve-project` / `register` / `get` / `set` / `remove` / `gitignore`（含 `--dry-run` 只读预览） |

## 目录结构

```
repo-committer/              # 本目录（可分发 / 安装的 skill）
├── SKILL.md                 # 技能定义（完整执行逻辑）
├── README.md                # 本文件
├── config.json              # 私有运行时偏好（已 gitignore；运行后生成、不随分发）
├── config.example.json      # 模板，随 skill 分发
├── scripts/
│   ├── _config_util.py      # 共享工具
│   ├── read_config.py
│   ├── write_config.py
│   ├── sync.py
│   ├── init_config.py
│   └── project.py
└── references/
    ├── conventional-commits.md
    ├── atomic-commits.md
    └── troubleshooting.md
```

## License

MIT，见仓库根目录 [LICENCE](../LICENCE)。