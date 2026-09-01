# architecture-printer

`architecture-printer` 是一个面向 Codex、Claude Code 及其他支持 `SKILL.md` 的代码代理的通用项目架构文档 Skill。

它帮助刚接触新项目的开发者快速回答：

- 项目从哪些 API、前端路由或 CLI 入口开始？
- 请求经过哪些编排层、服务和执行组件？
- 哪些依赖属于外部服务、数据库或配置边界？
- 修改一个功能时，应该从哪个文件和入口函数开始？

## 能力

- 扫描函数、类、公开 route、CLI/main 入口和 import 关系。
- 识别配置键及脱敏后的外部服务 host，不输出 `.env` 值、token 或密钥。
- 根据框架自动选择入口模板：FastAPI、Flask、Django、Express、NestJS、Next.js 或 generic。
- 生成单文件、可离线打开的交互式 HTML 架构图。
- 通过节点详情查看签名、docstring、源码 `file:line` 链接和证据状态。
- 保留无法确认的关系为 `[UNKNOWN: ...]`，避免凭经验编造调用链。

## 使用方式

在任意代码代理或终端中，从本目录执行：

```bash
python scripts/scan_project.py <target-dir> \
  --scope full \
  --framework auto \
  -o /tmp/architecture-scan.json

python scripts/render_workflow.py /tmp/architecture-scan.json \
  -o <target-dir>/docs/architecture/architecture-workflow.html
```

`--scope` 可选：`full`、`backend`、`frontend`、`integration`。

`--framework` 可选：`auto`、`generic`、`fastapi`、`flask`、`django`、`express`、`nestjs`、`next`。自动识别有歧义时，可以显式指定框架。

完整的 Skill 执行协议、质量门槛和异常处理见 [SKILL.md](SKILL.md)。Markdown 架构手册模板见 [references/architecture-template.md](references/architecture-template.md)，框架扩展规则见 [references/framework-routing.md](references/framework-routing.md)。

## 目录结构

```text
architecture-printer/
├── README.md
├── SKILL.md
├── scripts/
│   ├── scan_project.py         # 标准库扫描器
│   ├── frameworks.py           # 框架识别与模板路由
│   └── render_workflow.py      # 离线 HTML 渲染器
├── references/
│   ├── architecture-template.md
│   └── framework-routing.md
└── examples/
    └── predict_agent/
```

## 设计原则

1. 先扫描事实，再生成图和说明。
2. 框架特征只用于选择适配器，不用于推断不存在的调用链。
3. 未识别关系使用 `[UNKNOWN]`，不隐藏不确定性。
4. 只写入用户明确要求的架构输出，不修改目标项目业务代码。
5. 生成结果应纳入目标项目版本管理，但本工具不会自动执行 commit 或 push。

## License

本项目使用 [MIT License](LICENCE)。
