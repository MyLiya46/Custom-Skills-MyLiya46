# 常见异常场景与处理方案

本 skill 的异常处理总原则：**遇到异常 → 如实诊断 → 按方案处理 → 完成后向用户汇报**（汇报「发生了什么、怎么处理的、结果如何」，不把异常静默吞掉）。

## 1. config.json 不存在 / 缺字段

- **诊断**：`repo-committer/config.json` 不存在，或存在但缺 `current_user`。
- **方案**：优先 `scripts/init_config.py` 扫描本机 git 配置（--local → --global → --system，与 git 生效优先级一致）自动生成，避免提问；扫描不到有效身份才 `AskUserQuestion` 询问一次并写回。
- **汇报**：「已扫描本机 git 配置并生成 config.json，身份 = …」或「未扫到 git 身份，已询问并写回 …」。

## 2. 当前目录不是 git 仓库

- **诊断**：`git rev-parse --git-dir` 失败。
- **方案**：`behavior.auto_git_init` 为 true（缺省即 true）→ 自动 `git init`；为 false → 询问「是否初始化？」确认后 init。
- **汇报**：「当前目录尚未初始化 git，已自动 git init」或「已按你的确认执行 git init」。

## 3. 没有任何未提交改动

- **诊断**：`git status --porcelain` 为空。
- **方案**：友好提示「没有可提交的改动」，**结束，不报错**。
- **汇报**：「当前没有可提交的改动。」（一句话即可，不产出任何 commit）。

## 4. 单个文件混杂多个逻辑改动

- **诊断**：一个文件里同时含「新增功能」+「修另一个 bug」等无关改动。
- **方案**：默认按整文件归组；检测到明显混杂时提示「此文件似含多个逻辑改动」，询问是否用 `git add -p` 做 hunk 级拆分。
- **汇报**：列出「哪些文件混了哪几类改动 + 拆分结果」。

## 5. 生成 commit message 信息不足

- **诊断**：改动意图无法从 diff 判断（如重构动机、业务背景缺失）。
- **方案**：先列出缺失点，`AskUserQuestion` 提问，**不擅自臆测**。
- **汇报**：确认补齐后，回显最终 message。

## 6. 远端不存在（push 前）

- **诊断**：`remotes[]` 里的 `name` 在 `git remote` 中不存在。
- **方案**：`git remote add <name> <url>` 后再 push。
- **汇报**：「已新增 remote <name> → <url> 并推送」。

## 7. push 被远端拒绝

- **诊断**：`git push` 返回 non-fast-forward / 被拒等。
- **方案**：如实报告原因（远端有新提交需先 pull、无权限、分支保护等），**绝不自动 `git push --force`**；给出 `git pull --rebase` 等建议命令，交由用户决定。
- **汇报**：「push <name> 失败，原因：…；建议 …，需要我执行吗？」

## 8. 无远端（本地仓库从未配置 remote）

- **诊断**：`remotes` 为空且 `git remote` 为空。
- **方案**：仅完成本地提交，提示「未配置任何远程，请补充 remotes 或先 git remote add」。
- **汇报**：「已本地提交完成；未配置远程，未 push。可对我说『把 remotes 配好』或手动 git remote add。」

## 9. 提交身份邮箱不符合远端要求（如公司强制邮箱）

- **诊断**：push 因 commit 作者邮箱非法被拒（常见于企业 GitLab 的 push rules）。常见根因：本仓库 `--local` 配了不合规身份，或 committer config 里 `current_user.email` 记录的就是旧/错值。
- **方案**：说明原因，询问是否改用合规邮箱重写；重写走 `scripts/project.py set current_user.email …` 更新**当前项目**身份（只在用户要求改全局默认时，才用 `scripts/write_config.py`），不改写仓库 `.git/config`。
- **汇报**：「已把提交邮箱改为 …」或「未改，保持原身份」。

## 10. 本地 git 报错（lock 文件 / 索引损坏等）

- **诊断**：`git` 命令报 `.git/index.lock`、`fatal: ...` 等。
- **方案**：如实报告原始错误，给出常见处理（`rm .git/index.lock`、`git reset` 等），**不擅自执行破坏性命令**。
- **汇报**：「git 报错：…；可能原因 …；需要我执行 <命令> 吗？」

## 汇报模板（每次处理完异常后）

```
已处理：<场景>
原因：<一句话诊断>
处理：<做了什么 / 或给出建议命令>
结果：<成功 / 已跳过 / 待你确认>
```