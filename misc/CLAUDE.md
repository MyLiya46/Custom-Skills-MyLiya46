# Shell 环境选择规则（重要）

本机可用的命令行环境：
1. **Git Bash**（MSYS2 Bash）——默认首选，兼容 Linux 风格命令，适合绝大多数开发任务。
2. **PowerShell 7**（pwsh.exe）——仅在涉及 Windows 系统管理时使用。

## 默认使用 Git Bash
以下任务直接使用 Git Bash 语法（无需额外说明）：
- 文件与目录操作（`ls`, `cd`, `cp`, `mv`, `rm` 等）
- 运行脚本（Python、Node.js、shell 脚本等）
- 使用 `grep`, `sed`, `awk` 等文本处理工具
- 链式命令（`&&`, `||`, `;`）

## 何时切换到 PowerShell 7
仅当任务**明确且必须**涉及以下 Windows 系统级操作，且无法用 Git Bash 完成时，才生成 PowerShell 语法：
- 修改 Windows 注册表
- 管理 Windows 服务（启动/停止/查询）
- 调用仅限 Windows 的 COM 对象或 .NET 类型
- 使用仅在 PowerShell 中可用的模块（如 `ActiveDirectory`, `SqlServer` 等）

此时输出 PowerShell 语法，并注意：
- PowerShell 7 支持 `&&` 和 `||`，但建议保持脚本风格统一。
- 避免混用 Bash 特有命令（如 `grep` 应替换为 `Select-String`）。

## 禁止混用
- 在 Git Bash 中不要使用 PowerShell cmdlet（如 `Remove-Item`, `Get-ChildItem`）。
- 在 PowerShell 中不要使用 Bash 专用语法（如 `rm -rf` 应替换为 `Remove-Item -Recurse -Force`）
