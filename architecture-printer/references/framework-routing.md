# 框架模板路由

`scripts/frameworks.py` 是框架适配的唯一注册入口。扫描器不把某一个框架的目录结构当成全局事实，而是先选择框架，再选择模板和入口提取策略。

| 框架 ID | 识别证据 | 模板 ID | 重点入口 |
| --- | --- | --- | --- |
| `fastapi` | `fastapi` / `starlette` 依赖 | `python-api` | `app.get/post`、`router.get/post` 装饰器 |
| `flask` | `flask` 依赖 | `python-api` | `app.route`、`bp.route` 装饰器 |
| `django` | `django` 依赖或 `manage.py` | `django-layered` | `urls.py` 的 `path` / `re_path` |
| `express` | `express` 依赖 | `node-api` | `app.get/post`、`router.get/post` |
| `nestjs` | `@nestjs/*` 依赖 | `node-modular` | `@Get`、`@Post` 等装饰器 |
| `next` | `next` 依赖或 `next.config.*` | `next-fullstack` | `pages/api`、`app/api/**/route.*` 文件路由 |
| `generic` | 无充分证据 | `generic` | 通用函数、CLI、配置和 import 索引 |

## 扩展规则

1. 在 `FRAMEWORKS` 增加框架元数据、依赖关键词、语言和模板 ID。
2. 在 `detect_routes()` 增加只依赖语法证据的入口模式，并返回文件和行号。
3. handler 无法从静态语法追踪时返回 `[UNKNOWN: handler]`；不要用命名约定补全。
4. 用一个最小 fixture 项目测试自动识别、显式覆盖、重复 route 去重和未知 handler。
5. 如果模板只是视觉分组差异，复用渲染器；只有数据契约不同才增加独立模板分支。

`auto` 只写入框架 ID、模板 ID、识别模式和证据文件/关键词，不写依赖文件的完整内容；用户可用 `--framework` 覆盖自动结果。
