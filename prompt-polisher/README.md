# prompt-polisher

**版本**：v1.0.0（beta）

一个需求提示词打磨器：把**模糊、不专业、考虑不周全**的需求描述，打磨为**清晰、专业、完整、可验证**的 AI 提示词（prompt）。提高人与 AI 的沟通效率，降低反复修改需求的成本。

## 解决的问题

向 AI 描述需求时的常见痛点：

| 痛点 | 例子 |
|---|---|
| 模糊词泛滥 | "优化一下""好一点""尽快" |
| 目标不清 | 不说明给谁用、解决什么问题 |
| 关键维度缺失 | 无输出格式、无约束、无验收标准、无边界情况 |
| 隐式假设 | 想法没说出口，AI 只能猜方向 |

## 工作流程

```
解析输入 → 差距分析 → 澄清提问 → 综合优化 → 交付
```

1. **解析输入** — 提取目标、对象、场景、已有约束
2. **差距分析** — 对照 13 项完整性检查清单（目标/角色/背景/输入/任务/输出/约束/质量/边界/禁止/验收/示例/交互）找出缺口
3. **澄清提问** — 只问影响最大的 2-4 个问题（AskUserQuestion），其余以合理假设补齐并标注
4. **综合优化** — 产出结构化专业提示词，语言跟随用户输入
5. **交付** — 可直接复制的提示词 + 设计说明

## 特性

- **13 项完整性检查清单**：确保需求考虑周全，不缺关键维度
- **模糊词替换对照表**：「优化一下」→「响应时间 < 200ms、准确率 ≥ 95%」，量化代替感觉
- **Plan-then-execute**：复杂任务先交方案、确认后再动手，前置消灭方向性返工
- **提问权写入提示词**：生成内容内置「遇到歧义先确认」的交互约定
- **可验证的验收标准**：每条标准可检查，不做「界面好看」这类不可验收的承诺
- **基于前沿规范**：Anthropic / OpenAI 官方提示词工程文档与 2026 社区共识

## 安装

```bash
# 在本目录下执行；全局安装（所有项目可用）
# Claude Code
cp -r . ~/.claude/skills/prompt-polisher/
# Codex
cp -r . ~/.codex/skills/prompt-polisher/

# 或项目级安装（Claude Code）
cp -r . <目标项目>/.claude/skills/prompt-polisher/
# 项目级安装（Codex）
cp -r . <目标项目>/.codex/skills/prompt-polisher/
```

新开会话后（技能需重启后加载）：

- 输入 `/prompt-polisher` + 需求描述
- 或直接模糊地描述需求，描述中的触发词自动激活技能

## 文件结构

```
prompt-polisher/                          # 本目录（可分发 / 安装的 skill）
├── SKILL.md                              # 主指令：工作流程 + 检查清单 + 输出模板
└── references/
    ├── prompt-engineering-principles.md  # 提示词工程规范（15 条原则 + 模糊词替换对照表）
    └── example.md                        # 前后对比示例（任务类 + 问题类）
```

## 参考

- [Anthropic Prompt Engineering 文档](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
- [OpenAI Prompt Engineering Guide](https://platform.openai.com/docs/guides/prompt-engineering)

## License

MIT，见仓库根目录 [LICENCE](../LICENCE)。
