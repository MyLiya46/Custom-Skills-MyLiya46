# repo-committer

一个可复用的 Claude Code Skill：**一次性把当前项目所有未提交改动，按原子化最佳实践与 Conventional Commits 规范，分批次遣成一个清晰、可回滚、可追溯的提交序列**。

它做的事就一件：读懂 `git status` / `git diff` 里那些杂乱改动，把它们按「逻辑主题」切成一组一个 commit 的序列（原子化：一次只做一件事），每条 commit message 都符合 `type(scope): 主题`（主题语言由配置决定，默认中文），然后逐组精确提交，提交之后可选是否 push。**默认零询问直达结果，只在真正需要决策时才问**

---

## 安装

把本仓库的 `repo-committer/` 目录安装为你的 Claude Code skill：

```bash
git clone git@github.com:MyLiya46/repo-committer.git
cd repo-committer/

# 方式一：拷贝到用户级 skills 目录（示例路径，按你的实际环境调整）
mkdir -p ~/.claude/skills
cp -r repo-committer ~/.claude/skills/repo-committer

# 方式二：拷贝到项目级 skills 目录
cp -r repo-committer <your-project>/.claude/skills/repo-committer
```

Windows（PowerShell）等价命令：

```powershell
git clone git@github.com:MyLiya46/repo-committer.git
cd repo-committer

# 方式一：用户级 skills 目录
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item -Recurse -Force repo-committer "$env:USERPROFILE\.claude\skills\repo-committer"

# 方式二：项目级 skills 目录
Copy-Item -Recurse -Force repo-committer "<your-project>\.claude\skills\repo-committer"
```

安装后，在任意 git 仓库内对 Claude Code 说「（用 repo-committer）帮我提交改动」即可触发。更详细的用法与 `config.json` 字段说明见 [repo-committer/README.md](repo-committer/README.md)。

## 目录结构

```
repo-committer/
├── SKILL.md                # 技能定义（完整执行逻辑）
├── README.md               # 使用说明 + config.json 字段说明
├── config.json             # 用户运行时偏好（git 身份等）；本地生成，加入 .gitignore，不随 skill 分发
├── config.example.json     # 示例运行时偏好模板；随 skill 分发
├── scripts/                # 读取 / 修改 config.json 的脚本（_config_util.py / read_config.py / write_config.py / sync.py / init_config.py / project.py）
└── references/             # Conventional Commits 规范 + 原子化提交最佳实践 + 常见异常处理
```

## License

[MIT](LICENSE)