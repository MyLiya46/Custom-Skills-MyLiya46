# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A monorepo of **distributable Claude Code skills** (not an application). Each top-level directory is one independent, self-contained skill that gets copied into a client's skills directory (`~/.claude/skills/` or `<project>/.claude/skills/`) to be installed. There is no build, lint, test, or CI tooling anywhere — the only runnable code is a handful of Python-3-stdlib-only scripts inside two of the skills.

## Skill inventory

| Directory (repo) | Skill name (YAML frontmatter) | Nature |
|---|---|---|
| `architecture-printer/` | `architecture-printer` | Python scripts (scanner + renderer) |
| `plan-generator/` | `plan-generator` | Pure-prompt (SKILL.md + templates only) |
| `plan-executor/` | `plan-executor` | Pure-prompt (SKILL.md + templates; downstream of plan-generator) |
| `prompt-polisher/` | `prompt-polisher` | Pure-prompt |
| `repo-committer/` | `repo-committer` | Python scripts (config + gitignore scan) |

Directory names strictly match their skill `name:` frontmatter field.

## Skill packaging convention

Every installable skill follows the same layout convention:

- `SKILL.md` — YAML frontmatter (`name` + `description`) at top, then the executable protocol in prose/Chinese.
- `references/*.md` — supporting material the skill loads on demand (templates, checklists, error-handling tables). Not loaded eagerly.
- `scripts/*.py` — only where the skill is script-driven (`architecture-printer`, `repo-committer`).

The `references/` and `SKILL.md` go together: `SKILL.md` states the workflow and quality gates, and points into `references/` for the detailed template/table it defers to. When editing one, check whether the other references the same facts (e.g. task status enums appear in both `plan-generator/SKILL.md` and `references/plan-template.md`).

## Commands

No build/test/lint exists. The runnable Python scripts are stdlib-only (no requirements, no virtualenv needed). They are invoked with the bare interpreter, resolved cross-platform as `python` / `python3` / `py` (whichever is available) — see the `repo-committer` SKILL.md convention.

**architecture-printer** (run from inside `architecture-printer/`):

```bash
python scripts/scan_project.py <target-dir> --scope full --framework auto -o /tmp/architecture-scan.json
python scripts/render_workflow.py /tmp/architecture-scan.json -o <target-dir>/docs/architecture/architecture-workflow.html
```

`--scope`: `full` | `backend` | `frontend` | `integration`. `--framework`: `auto` | `generic` | `fastapi` | `flask` | `django` | `express` | `nestjs` | `next`.

**repo-committer** (run from inside `repo-committer/repo-committer/`):

```bash
python scripts/init_config.py                # scan git identity + remotes → config.json
python scripts/project.py resolve-project    # print current project root (upward .git search)
python scripts/project.py gitignore --dry-run   # read-only privacy scan of pending changes
```

All `repo-committer` scripts take absolute paths and do not depend on the runtime working directory.

## Architecture of the scripted skills

**architecture-printer** is a two-stage, evidence-first pipeline:

1. `scripts/scan_project.py` walks a target project (stdlib `ast` for Python, regex for JS/TS and config files), emitting a single JSON document — `framework` (id/template/mode/evidence), `declarations`, `routes`, `edges` (import graph), `configs` (keys + redacted hosts only). Unresolved imports are emitted as `[UNKNOWN: module]`, never guessed.
2. `scripts/frameworks.py` holds the framework registry (`FRAMEWORKS` dict) mapping dependency/file evidence → framework ID → template ID → route-extraction strategy. `detect_routes()` only records routes provable from syntax; untraceable handlers become `[UNKNOWN: handler]`.
3. `scripts/render_workflow.py` renders the JSON to a single self-contained interactive HTML file (no CDN; `<noscript>` fallback), grouping nodes into five Chinese-labeled layers (入口/编排/执行与外部/数据与配置/外部依赖).

Extending it always means adding a `FRAMEWORKS` entry first, then a minimal `detect_routes()` pattern — never inferring a route from directory naming. See `references/framework-routing.md`.

**repo-committer** builds on a shared helper, `scripts/_config_util.py` (config.json locate/read/write/dotted-path get/set), wrapped by thin single-purpose scripts (`read_config.py`, `write_config.py`, `sync.py`, `init_config.py`, `project.py`). Its core model is two distinct configs: **git config** (machine truth) vs **committer config** (`config.json`, repo-committer's own "project memory" keyed by *absolute project root path*). The `project.py gitignore --dry-run` step is a read-only privacy interceptor that runs before every `git add`, delegating ignore resolution to `git check-ignore`.

## Cross-cutting rules worth knowing

- **Output language is Chinese by default** across all four skills (documentation, generated artifacts' labels, commit messages via `commit_message_language: "zh"`). Follow the target project's language only where the skill says so explicitly (e.g. `architecture-printer` Markdown follows the project's main language).
- **Secret hygiene is a hard invariant** in the scripted skills: scan/render never emits `.env` values, tokens, keys, or full config values — only key *names* and redacted hosts. `repo-committer` likewise never reads/writes SSH keys or credentials (auth stays in git's own config).
- **Evidence over inference**: `architecture-printer` marks any unprovable relationship `[UNKNOWN: …]` rather than fabricating a call chain. Preserve this convention when editing the pipeline — unresolved entities must stay explicitly marked, not silently "completed".
- **`repo-committer` forbids `Co-Authored-By`/`Claude`/any agent signature** in commit messages, never uses `git add .`, and injects commit identity via `git -c user.name=… -c user.email=…` rather than rewriting `.git/config`. Do not violate these when extending or editing its scripts.