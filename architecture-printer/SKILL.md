---
name: architecture-printer
description: Scan an unfamiliar project and produce an evidence-backed interactive HTML architecture workflow plus a Markdown onboarding guide. Use from Codex, Claude Code, or another file-aware coding agent when a developer needs to understand a new codebase, map public routes and entry points, trace imports and service orchestration, document external dependencies/configuration/database layers, or refresh architecture docs. Supports framework-routed templates for FastAPI, Flask, Django, Express, NestJS, Next.js, and generic projects; never invents unresolved imports or exposes secrets.
---

# Architecture Printer

把陌生代码库转换成“新开发者一天内可上手”的架构速查资料。先扫描事实，再分析链路，最后生成图和说明；任何无法由扫描结果或源码确认的关系都必须标记为 `[UNKNOWN: name]`，不得猜测。Skill 本身只依赖 `SKILL.md`、Python 标准库和 shell 命令，不依赖某个 AI 厂商的工具调用协议；Codex、Claude Code 等宿主只需把下列命令作为普通终端命令执行。

## 输入与输出

- `target_dir`：用户指定的项目根目录；未明确且当前目录不明显时先询问。默认可用 `./`。
- `scan_scope`：默认为 `full`，可选 `backend`、`frontend`、`integration`。
- `framework`：默认为 `auto`，可选 `generic`、`fastapi`、`flask`、`django`、`express`、`nestjs`、`next`。`auto` 根据依赖文件和框架特征选择模板路由；无法确认时使用 `generic`。
- 输出目录：`<target_dir>/docs/architecture/`。
- 输出文件：`architecture-workflow.html` 与 `<project-name>-ARCHITECTURE.md`。允许覆盖同一目录中由本 Skill 生成的旧版本；不改写其他文档或业务代码。

## 标准工作流

### 1. 确认边界

确认目标根目录、扫描范围和目标项目语言。读取项目级 `CLAUDE.md`、`README*`、架构文档和构建配置作为解释依据，但优先以源码和扫描结果为事实来源。不要读取或输出 `.env` 的值、密钥、token、Cookie、Authorization header 或完整配置值。

若目标项目文件超过 5000 个，只扫描 `src/`、`app/`、`backend/`、`frontend/`、`main.*`、入口测试及配置文件，明确记录缩减范围。

### 2. 运行确定性扫描

在 Skill 目录中执行：

```text
python scripts/scan_project.py <target_dir> --scope <scan_scope> --framework auto -o <temporary-scan.json>
```

脚本只使用 Python 标准库，输出 JSON，包含 `framework.id`、`framework.template`、识别证据、模块、函数/类声明、路由、导入边、配置键和脱敏服务提示。解析不到的 import 使用 `[UNKNOWN: module.name]`。扫描失败时不要提前生成图，先用 `rg`/源码阅读完成最小事实清单，并在最终文档的“限制与待确认项”中说明降级原因。

框架路由遵循 `scripts/frameworks.py` 的注册表：依赖/文件特征 → 框架 ID → 模板 ID → route 提取器。显式 `--framework fastapi` 等选项用于项目依赖不规范或自动识别有歧义的情况。新增框架时先增加注册元数据，再增加最小可证明的入口模式；没有证据的 handler 保持 `[UNKNOWN: handler]`。

### 3. 审核扫描结果

检查以下事实是否存在并去重：

- `main`、CLI、前端路由、API route 等公开入口及其文件行号。
- 函数签名、类声明、docstring 摘要和源码相对路径。
- import 有向边；未解析边必须保留 `[UNKNOWN: ...]`，不可用目录命名补全。
- 配置键、外部服务名及脱敏后的 host/path；不输出配置值。
- 公开 route 是否能回溯到上游模块入口。无法证明的边标 `[UNKNOWN: upstream]`。

如果某个层的节点超过 80 个，渲染器必须显示“XXX 子系统”折叠组，同时在组内保留所有节点，不能静默丢弃公开入口。

### 4. 生成交互式 HTML

```text
python scripts/render_workflow.py <temporary-scan.json> -o <target_dir>/docs/architecture/architecture-workflow.html
```

生成页面必须是单文件、离线可打开，且在页眉显示实际使用的框架和模板路由，至少具备：

- 入口、编排、执行/外部、数据、返回五个可折叠层级，并且每层有明确 label。
- 全局链路概览 + 可点击节点详情；详情包含签名、docstring、源码 `file:line` 链接、依赖和证据状态。
- 图例：内部/外部使用不同颜色；同步实线、异步虚线。扫描未能确认异步关系时，明确写“未确认”，不能把同步 import 推断成异步调用。
- 搜索或过滤能力；无 CDN 依赖。若采用 CDN，必须提供 `<noscript>` 或纯文本核心链路降级。
- 不渲染真实业务结果数字；示例值标记“※ 示意数据，非真实业务数据”。

### 5. 生成 Markdown 速查手册

阅读扫描 JSON 和目标项目文档后，生成 `<project-name>-ARCHITECTURE.md`。直接参考 [references/architecture-template.md](references/architecture-template.md)，必须包含：架构总览、技术栈、核心链路图解、模块索引、外部依赖、数据层/配置路径、常见任务上手路径、限制与待确认项。正文至少 500 个中文字符，不能只有表格；没有证据的内容写 `[待确认: 描述]`。

### 6. 质量自检与交付

逐项核对后再交付：

- [ ] HTML 可离线打开，脚本无语法错误，五层均有 label 和折叠入口。
- [ ] 扫描结果中的 import 已去重；没有把 `[UNKNOWN]` 改成猜测的模块名。
- [ ] 100% 已识别的公开 route、main/CLI 入口出现在图或可展开索引中；最终覆盖率统计写入 Markdown。
- [ ] 外部依赖至少有服务名或脱敏 URL/host；鉴权方式只写变量名/机制，不写值。
- [ ] 函数签名、docstring 和源码行号链接来自扫描结果，没有输出完整源文件。
- [ ] Markdown 字数不少于 500；表格列对齐，语言跟随项目主要语言。
- [ ] 输出目录之外没有写入；未执行 `git add`、`commit` 或修改业务逻辑。
- [ ] JSON 中的 `framework.mode`、`framework.evidence` 与最终 Markdown 的技术栈说明一致。

临时 JSON 用完后删除；如果用户要求保留扫描数据，先确认其位置和敏感信息风险。交付时简述扫描范围、输出路径、覆盖率、降级情况和待确认项。最多接受两轮针对折叠粒度或链路细节的迭代。

## 异常处理

- `target_dir` 不明确：询问，不擅自扫描当前目录。
- `scan_project.py` 缺失/失败：使用 `rg --files`、`rg "@(app|router)|def main|import "` 等只读搜索补齐事实，并标注“扫描降级”。
- 非 Python Web 项目：优先使用 `express`、`nestjs`、`next` 或 `generic` 路由；保留通用文件/配置/导入扫描。在 Markdown 标明语言策略，函数签名未识别处写 `[UNKNOWN: signature]`，不要套用 Python 规则。
- 生成目录已存在：只覆盖本 Skill 的两个明确输出文件；发现其他文件或命名冲突时先询问。

## 资源

- `scripts/scan_project.py`：标准库静态扫描器，支持 Python 与常见前端文件。
- `scripts/frameworks.py`：框架识别、模板路由和入口模式适配器注册表。
- `scripts/render_workflow.py`：单文件离线 HTML 渲染器。
- `references/architecture-template.md`：Markdown 交付模板和证据表达规范。
- `references/framework-routing.md`：框架路由表、扩展约定和测试规则。
