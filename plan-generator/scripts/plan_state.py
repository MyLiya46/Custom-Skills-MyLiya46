#!/usr/bin/env python3
"""Fast reader, validator, query tool, and atomic status writer for docs/todo.md.

The same implementation is shipped with plan-executor so either skill remains
independently installable. Keep the two copies in sync.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


STATUSES = ("pending", "reviewed", "in_progress", "completed", "blocked")
STATUS_RE = re.compile(r"\b(?:pending|reviewed|in_progress|completed|blocked)\b")
TASK_ROW_RE = re.compile(r"^\|\s*\d+\s*\|\s*T\d+\s*\|")
TASK_ID_RE = re.compile(r"^T\d+$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PLAN_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
PLAN_STEP_RE = re.compile(r"^###\s+(.+?)\s*$")
PLAN_FIELD_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?([^：:\s]+)(?:\*\*)?\s*[：:]\s*(.*?)\s*$"
)
PLAN_ID_RE = re.compile(r"任务\s*ID\s*[：:]\s*(T\d+)")
PLAN_BLOCKED_BY_RE = re.compile(r"blockedBy\s*[：:]\s*(.*)", re.IGNORECASE)
FORBIDDEN_PLAN_RE = re.compile(r"\b(?:TBD|TODO)\b", re.IGNORECASE)
REQUIRED_PLAN_SECTIONS = ("问题", "决策", "范围", "环境预检", "风险与回滚", "实施步骤", "完成标准")
LEGACY_PLAN_SECTIONS = ("问题", "决策", "范围", "风险与回滚", "实施步骤", "完成标准")
PLAN_STEP_FIELDS = ("对象", "动作", "参数", "核心修改文件", "必要集成文件", "命令")
ACCEPTANCE_TYPES = {"offline", "external", "mixed"}
EXECUTION_PROFILES = {"short", "normal", "long-infra", "external", "e2e"}
JSON_SCHEMA_VERSION = 1
DEFAULT_STATE_FILE = Path("docs/todo.json")
DEFAULT_TODO_VIEW = Path("docs/todo.md")
GENERATED_MARKER = "<!-- GENERATED FROM docs/todo.json; DO NOT EDIT -->"

ALLOWED_TRANSITIONS = {
    "pending": {"pending", "reviewed", "blocked"},
    "reviewed": {"reviewed", "in_progress", "completed", "blocked"},
    "in_progress": {"in_progress", "completed", "blocked"},
    "completed": {"completed"},
    "blocked": {"blocked", "reviewed"},
}


class StateError(Exception):
    """A user-correctable todo/state error."""


@dataclass
class Task:
    task_id: str
    title: str
    blocked_by: list[str]
    status: str
    plan_link: str
    row_index: int

    def public(self, todo_path: Path) -> dict[str, object]:
        plan_path = str((todo_path.parent / self.plan_link).as_posix())
        return {
            "task_id": self.task_id,
            "title": self.title,
            "blocked_by": self.blocked_by,
            "status": self.status,
            "plan": plan_path,
        }


@dataclass
class TodoState:
    path: Path
    text: str
    lines: list[str]
    tasks: list[Task]
    parse_errors: list[str]


def _plan_sections(text: str) -> list[tuple[str, int, list[str]]]:
    lines = text.splitlines()
    headings: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = PLAN_HEADING_RE.match(line)
        if match:
            headings.append((match.group(1).strip(), index))
    return [
        (name, line_number + 1, lines[line_number + 1 : headings[pos + 1][1] if pos + 1 < len(headings) else len(lines)])
        for pos, (name, line_number) in enumerate(headings)
    ]


def _field_value(lines: list[str], label: str) -> str | None:
    pattern = re.compile(rf"^\s*(?:[-*]\s*)?(?:\*\*)?{re.escape(label)}(?:\*\*)?\s*[：:]\s*(.*?)\s*$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip()
        if value:
            return value
        following_lines = lines[index + 1 :]
        for following_index, following in enumerate(following_lines):
            candidate = following.strip()
            if not candidate:
                continue
            if candidate.startswith("```"):
                code: list[str] = []
                for code_line in following_lines[following_index + 1 :]:
                    stripped = code_line.strip()
                    if stripped.startswith("```"):
                        break
                    if stripped:
                        code.append(stripped)
                return " ".join(code).strip()
            if PLAN_FIELD_RE.match(following):
                break
            return candidate
        return ""
    return None


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    return bool(re.fullmatch(r"(?:[（(].*[）)]|<.*>|\.\.\.)", value.strip()))


def _has_real_content(lines: list[str]) -> bool:
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        field = PLAN_FIELD_RE.match(stripped)
        value = field.group(2).strip() if field else stripped.lstrip("-* ").strip()
        if not _is_placeholder(value):
            return True
    return False


def lint_plan(plan_file: Path, expected_task: str | None = None, expected_blocked_by: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    try:
        text = plan_file.read_text(encoding="utf-8")
    except OSError as error:
        return [f"{plan_file}: cannot read plan: {error}"]

    prefix = str(plan_file)
    if FORBIDDEN_PLAN_RE.search(text):
        errors.append(f"{prefix}: forbidden TBD/TODO token")

    sections = _plan_sections(text)
    positions: dict[str, list[tuple[int, list[str]]]] = {}
    for name, line_number, body in sections:
        if name in REQUIRED_PLAN_SECTIONS:
            positions.setdefault(name, []).append((line_number, body))

    legacy_format = "环境预检" not in positions
    required_sections = LEGACY_PLAN_SECTIONS if legacy_format else REQUIRED_PLAN_SECTIONS
    missing = [name for name in required_sections if name not in positions]
    if missing:
        errors.append(f"{prefix}: missing required sections: {', '.join(missing)}")
    duplicates = [name for name, values in positions.items() if name in required_sections and len(values) > 1]
    if duplicates:
        errors.append(f"{prefix}: duplicate required sections: {', '.join(sorted(duplicates))}")
    ordered = [name for name, _, _ in sections if name in required_sections]
    expected_order = [name for name in required_sections if name in positions]
    if ordered != expected_order:
        errors.append(
            f"{prefix}: required sections are out of order; expected {' -> '.join(required_sections)}"
        )

    for name in required_sections:
        values = positions.get(name, [])
        if values and not _has_real_content(values[0][1]):
            errors.append(f"{prefix}: section {name} is empty")

    risk_body = positions.get("风险与回滚", [(0, [])])[0][1]
    for field in ("风险", "回滚"):
        if _is_placeholder(_field_value(risk_body, field)):
            errors.append(f"{prefix}: 风险与回滚 must include a non-empty {field} field")

    if not legacy_format:
        environment_body = positions.get("环境预检", [(0, [])])[0][1]
        for field in (
            "必需 Shell",
            "必需命令",
            "必需端口",
            "必需 URL",
            "必需 Python 模块",
            "必需 Docker 容器",
            "容器内必需命令",
            "容器内必需 Python 模块",
            "执行画像",
            "启动超时（秒）",
            "空闲超时（秒）",
            "硬截止（秒）",
            "最大 checkpoint 间隔（秒）",
            "预检命令",
        ):
            if _is_placeholder(_field_value(environment_body, field)):
                errors.append(f"{prefix}: 环境预检 must include a non-empty {field} field")
        profile = _field_value(environment_body, "执行画像")
        if profile and profile not in EXECUTION_PROFILES:
            errors.append(f"{prefix}: 环境预检 执行画像 must be one of {', '.join(sorted(EXECUTION_PROFILES))}")
        for timeout_field in ("启动超时（秒）", "空闲超时（秒）", "硬截止（秒）", "最大 checkpoint 间隔（秒）"):
            value = _field_value(environment_body, timeout_field)
            if value and not re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
                errors.append(f"{prefix}: 环境预检 {timeout_field} must be a positive number of seconds")

    step_values = positions.get("实施步骤", [])
    if step_values:
        step_lines = step_values[0][1]
        step_heads = [index for index, line in enumerate(step_lines) if PLAN_STEP_RE.match(line)]
        if not step_heads:
            errors.append(f"{prefix}: 实施步骤 must contain at least one ### step")
        for position, start in enumerate(step_heads):
            end = step_heads[position + 1] if position + 1 < len(step_heads) else len(step_lines)
            body = step_lines[start + 1 : end]
            title = PLAN_STEP_RE.match(step_lines[start]).group(1).strip()  # type: ignore[union-attr]
            step_fields = ("对象", "动作", "参数", "文件", "命令") if legacy_format else PLAN_STEP_FIELDS
            for field in step_fields:
                if _is_placeholder(_field_value(body, field)):
                    errors.append(f"{prefix}: step {title} must include a non-empty {field} field")
            file_fields = ("文件",) if legacy_format else ("核心修改文件", "必要集成文件")
            for file_field in file_fields:
                file_value = _field_value(body, file_field)
                if file_value and (
                    file_field in {"文件", "核心修改文件"}
                    and file_value.strip() in {"无", "N/A", "n/a"}
                    or
                    re.match(r"^(?:[A-Za-z]:[\\/]|[\\/])", file_value.strip())
                    or file_value.strip().startswith("..")
                ):
                    errors.append(f"{prefix}: step {title} {file_field} must be a relative path")

    completion_body = positions.get("完成标准", [(0, [])])[0][1]
    acceptance_type = _field_value(completion_body, "验收类型")
    if legacy_format:
        if _is_placeholder(_field_value(completion_body, "验收命令")):
            errors.append(f"{prefix}: 完成标准 must include a runnable 验收命令")
    else:
        if acceptance_type not in ACCEPTANCE_TYPES:
            errors.append(f"{prefix}: 完成标准 验收类型 must be one of offline, external, mixed")
        if _is_placeholder(_field_value(completion_body, "离线验收命令")):
            errors.append(f"{prefix}: 完成标准 must include a runnable 离线验收命令")
        if acceptance_type in {"external", "mixed"} and _is_placeholder(_field_value(completion_body, "外部环境验收命令")):
            errors.append(f"{prefix}: external or mixed plans require 外部环境验收命令")
    if _is_placeholder(_field_value(completion_body, "通过条件")):
        errors.append(f"{prefix}: 完成标准 must include a non-empty 通过条件 field")

    metadata_match = PLAN_ID_RE.search(text)
    if not metadata_match:
        errors.append(f"{prefix}: missing task ID metadata")
    elif expected_task and metadata_match.group(1) != expected_task:
        errors.append(f"{prefix}: plan task ID does not match todo task {expected_task}")
    blocked_match = PLAN_BLOCKED_BY_RE.search(text)
    if not blocked_match:
        errors.append(f"{prefix}: missing blockedBy metadata")
    elif expected_blocked_by is not None:
        actual = sorted(set(re.findall(r"T\d+", blocked_match.group(1))))
        expected = sorted(set(expected_blocked_by))
        if actual != expected:
            errors.append(
                f"{prefix}: blockedBy does not match todo: plan={actual or ['无']}, todo={expected or ['无']}"
            )
    return errors


def load_state(todo_path: Path) -> TodoState:
    if not todo_path.is_file():
        raise StateError(f"todo file not found: {todo_path}")

    with todo_path.open("r", encoding="utf-8", newline="") as stream:
        text = stream.read()
    lines = text.splitlines(keepends=True)
    tasks: list[Task] = []
    errors: list[str] = []

    for row_index, line in enumerate(lines):
        if not TASK_ROW_RE.match(line):
            continue
        cells = line.rstrip("\r\n").split("|", 7)
        if len(cells) < 8:
            errors.append(f"line {row_index + 1}: task row has fewer than 7 columns")
            continue

        task_id = cells[2].strip()
        title = cells[3].strip()
        dependency_text = cells[4].strip()
        status_match = STATUS_RE.search(cells[5])
        status = status_match.group(0) if status_match else ""
        link_match = LINK_RE.search(cells[7])
        plan_link = link_match.group(1).strip() if link_match else ""

        blocked_by = []
        if dependency_text and dependency_text != "无":
            blocked_by = re.findall(r"T\d+", dependency_text)

        tasks.append(
            Task(
                task_id=task_id,
                title=title,
                blocked_by=blocked_by,
                status=status,
                plan_link=plan_link,
                row_index=row_index,
            )
        )

    return TodoState(todo_path, text, lines, tasks, errors)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_tasks(data: dict[str, object]) -> list[Task]:
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    tasks: list[Task] = []
    for item in raw_tasks:
        if not isinstance(item, dict):
            continue
        blocked_by = item.get("blockedBy", [])
        tasks.append(
            Task(
                task_id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                blocked_by=[str(value) for value in blocked_by] if isinstance(blocked_by, list) else [],
                status=str(item.get("status", "")),
                plan_link=str(item.get("plan", "")),
                row_index=-1,
            )
        )
    return tasks


def todo_state_from_json(path: Path, data: dict[str, object]) -> TodoState:
    return TodoState(path, "", [], json_tasks(data), [])


def load_json_state(state_path: Path) -> tuple[dict[str, object], TodoState]:
    if not state_path.is_file():
        raise StateError(f"state file not found: {state_path}")
    try:
        with state_path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateError(f"cannot read state file: {state_path}: {error}") from error
    if not isinstance(data, dict):
        raise StateError(f"state file must contain a JSON object: {state_path}")
    return data, todo_state_from_json(state_path, data)


def validate_json_state(
    state_path: Path,
    data: dict[str, object],
    *,
    check_files: bool = True,
    check_plans: bool = True,
) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != JSON_SCHEMA_VERSION:
        errors.append(f"{state_path}: unsupported schema_version")
    revision = data.get("revision")
    if not isinstance(revision, int) or revision < 0:
        errors.append(f"{state_path}: revision must be a non-negative integer")
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return errors + [f"{state_path}: tasks must be a list"]
    for index, item in enumerate(raw_tasks, 1):
        if not isinstance(item, dict):
            errors.append(f"{state_path}: task {index} must be an object")
            continue
        for key in ("id", "title", "blockedBy", "plan", "status"):
            if key not in item:
                errors.append(f"{state_path}: task {index} missing {key}")
        if "blockedBy" in item and not isinstance(item.get("blockedBy"), list):
            errors.append(f"{state_path}: task {index} blockedBy must be a list")
        if item.get("status") == "blocked":
            if not item.get("blockedReason"):
                errors.append(f"{state_path}: task {index} blocked status requires blockedReason")
            if not item.get("resumeFrom"):
                errors.append(f"{state_path}: task {index} blocked status requires resumeFrom")
    errors.extend(validate_state(todo_state_from_json(state_path, data), check_files, check_plans))
    return sorted(set(errors))


def markdown_to_json(todo_path: Path, *, check_plans: bool = True) -> dict[str, object]:
    state = load_state(todo_path)
    errors = validate_state(state, check_plans=check_plans)
    if errors:
        raise StateError("todo validation failed:\n- " + "\n- ".join(errors))
    if state.tasks:
        first = state.tasks[0].row_index
        last = state.tasks[-1].row_index
        prefix = "".join(state.lines[:first])
        suffix = "".join(state.lines[last + 1 :])
    else:
        prefix = state.text
        suffix = ""
    tasks: list[dict[str, object]] = []
    for task in state.tasks:
        item: dict[str, object] = {
            "id": task.task_id,
            "title": task.title,
            "blockedBy": task.blocked_by,
            "plan": task.plan_link.replace("\\", "/"),
            "status": task.status,
        }
        if task.status == "blocked":
            item["blockedReason"] = "legacy markdown import: original reason unavailable"
            item["resumeFrom"] = "review"
        tasks.append(item)
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "revision": 0,
        "updated_at": utc_now(),
        "tasks": tasks,
        "markdown_view": {"prefix": prefix, "suffix": suffix},
    }


def render_markdown(data: dict[str, object], state_path: Path, todo_path: Path) -> str:
    view = data.get("markdown_view")
    view = view if isinstance(view, dict) else {}
    prefix = str(view.get("prefix", ""))
    suffix = str(view.get("suffix", ""))
    if GENERATED_MARKER not in prefix:
        prefix = GENERATED_MARKER + "\n" + prefix
    if "| 序号 |" not in prefix and "| No |" not in prefix:
        prefix += (
            "| 序号 | 任务ID | 标题 | blockedBy | 状态 | 负责人 | 计划 |\n"
            "|---|---|---|---|---|---|---|\n"
        )
    rows: list[str] = []
    raw_tasks = data.get("tasks")
    for index, item in enumerate(raw_tasks if isinstance(raw_tasks, list) else [], 1):
        if not isinstance(item, dict):
            continue
        plan = str(item.get("plan", ""))
        try:
            absolute_plan = (state_path.parent / plan).resolve()
            plan = os.path.relpath(absolute_plan, todo_path.parent.resolve()).replace(os.sep, "/")
        except (OSError, ValueError):
            pass
        blocked = item.get("blockedBy", [])
        blocked_text = "、".join(str(value) for value in blocked) if isinstance(blocked, list) else ""
        rows.append(
            f"| {index} | {item.get('id', '')} | {str(item.get('title', '')).replace('|', '/')} | "
            f"{blocked_text or '无'} | {item.get('status', '')} | - | [plan]({plan}) |\n"
        )
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += "\n"
    if rows and suffix and not suffix.startswith(("\n", "\r")):
        suffix = "\n" + suffix
    return prefix + "".join(rows) + suffix


def plan_path(state: TodoState, task: Task) -> Path:
    if not task.plan_link:
        raise StateError(f"{task.task_id}: plan link is missing")
    return (state.path.parent / task.plan_link.replace("/", os.sep)).resolve()


def validate_plan_documents(state: TodoState, tasks: Iterable[Task] | None = None) -> list[str]:
    errors: list[str] = []
    seen_paths: dict[Path, str] = {}
    for task in tasks or state.tasks:
        if not task.plan_link:
            continue
        if Path(task.plan_link).is_absolute():
            continue
        path = plan_path(state, task)
        try:
            path.relative_to(state.path.parent.resolve())
        except ValueError:
            continue
        if path in seen_paths and seen_paths[path] != task.task_id:
            errors.append(f"{task.task_id}: plan path is also used by {seen_paths[path]}: {task.plan_link}")
        seen_paths[path] = task.task_id
        if not path.is_file():
            continue
        errors.extend(lint_plan(path, task.task_id, task.blocked_by))
    return errors


def validate_state(state: TodoState, check_files: bool = True, check_plans: bool = True) -> list[str]:
    errors = list(state.parse_errors)
    by_id: dict[str, Task] = {}

    for task in state.tasks:
        if not TASK_ID_RE.match(task.task_id):
            errors.append(f"{task.task_id or '<empty>'}: invalid task ID")
        if task.task_id in by_id:
            errors.append(f"{task.task_id}: duplicate task ID")
        by_id[task.task_id] = task
        if task.status not in STATUSES:
            errors.append(f"{task.task_id}: invalid status {task.status or '<empty>'}")
        if not task.plan_link:
            errors.append(f"{task.task_id}: missing plan link")
        elif Path(task.plan_link).is_absolute():
            errors.append(f"{task.task_id}: plan path must be relative: {task.plan_link}")
        elif check_files:
            resolved_plan = plan_path(state, task)
            try:
                resolved_plan.relative_to(state.path.parent.resolve())
            except ValueError:
                errors.append(f"{task.task_id}: plan path escapes todo directory: {task.plan_link}")
            if not resolved_plan.is_file():
                errors.append(f"{task.task_id}: plan file not found: {task.plan_link}")
            elif not resolved_plan.name.startswith(task.task_id):
                errors.append(f"{task.task_id}: plan filename does not start with task ID: {task.plan_link}")

    for task in state.tasks:
        for dependency in task.blocked_by:
            if dependency not in by_id:
                errors.append(f"{task.task_id}: dependency not found: {dependency}")

    graph = {task.task_id: task.blocked_by for task in state.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str, chain: list[str]) -> None:
        if task_id in visiting:
            cycle = " -> ".join(chain + [task_id])
            errors.append(f"dependency cycle: {cycle}")
            return
        if task_id in visited or task_id not in graph:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency, chain + [task_id])
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id, [])

    if check_files and check_plans:
        errors.extend(validate_plan_documents(state))
    return sorted(set(errors))


def get_tasks(state: TodoState, task_ids: Iterable[str] | None) -> list[Task]:
    if not task_ids:
        return state.tasks
    wanted = list(task_ids)
    by_id = {task.task_id: task for task in state.tasks}
    missing = [task_id for task_id in wanted if task_id not in by_id]
    if missing:
        raise StateError("task not found: " + ", ".join(missing))
    return [by_id[task_id] for task_id in wanted]


def ready_tasks(state: TodoState) -> list[Task]:
    errors = validate_state(state)
    if errors:
        raise StateError("todo validation failed:\n- " + "\n- ".join(errors))
    by_id = {task.task_id: task for task in state.tasks}
    return [
        task
        for task in state.tasks
        if task.status in {"reviewed", "in_progress"}
        and all(by_id[dependency].status == "completed" for dependency in task.blocked_by)
    ]


def format_tasks(tasks: list[Task], state: TodoState, output_format: str) -> str:
    payload = [task.public(state.path) for task in tasks]
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=True, indent=2)
    lines = ["TASK\tSTATUS\tBLOCKED_BY\tPLAN\tTITLE"]
    for task in tasks:
        lines.append(
            "\t".join(
                [
                    task.task_id,
                    task.status,
                    ",".join(task.blocked_by) or "-",
                    task.plan_link or "-",
                    task.title,
                ]
            )
        )
    return "\n".join(lines)


def replace_status(line: str, status: str) -> str:
    newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    body = line[: -len(newline)] if newline else line
    cells = body.split("|", 7)
    if len(cells) < 8:
        raise StateError("cannot rewrite malformed task row")
    cells[5] = STATUS_RE.sub(status, cells[5], count=1)
    return "|".join(cells) + newline


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


@contextmanager
def state_lock(path: Path, timeout: float = 30.0, stale_after: float = 300.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    started = time.monotonic()
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()}\ncreated={utc_now()}\n".encode("utf-8"))
            os.close(descriptor)
            descriptor = None
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    lock_path.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() - started >= timeout:
                raise StateError(f"state lock timeout: {lock_path}")
            time.sleep(0.05)
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
            raise StateError(f"cannot create state lock: {lock_path}: {error}") from error
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise StateError(f"cannot remove state lock: {lock_path}: {error}") from error


def write_json_state(path: Path, data: dict[str, object]) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=True, indent=2) + "\n")


def sync_markdown_view(state_path: Path, data: dict[str, object], todo_path: Path | None) -> Path:
    """Keep the generated view adjacent to the canonical state after a write."""
    target = (todo_path or state_path.parent / DEFAULT_TODO_VIEW.name).expanduser().resolve()
    atomic_write(target, render_markdown(data, state_path, target))
    return target


def backend_is_markdown(args: argparse.Namespace) -> bool:
    return args.todo is not None


def state_path_from_args(args: argparse.Namespace) -> Path:
    return Path(args.state).expanduser()


def json_transition(
    args: argparse.Namespace,
    *,
    status: str | None = None,
    from_status: str | None = None,
) -> int:
    path = state_path_from_args(args).resolve()
    synced_todo: Path | None = None
    sync_warning: str | None = None
    with state_lock(path):
        data, state = load_json_state(path)
        errors = validate_json_state(path, data)
        if errors:
            raise StateError("state validation failed:\n- " + "\n- ".join(errors))
        revision = data.get("revision")
        if args.if_revision is not None and revision != args.if_revision:
            raise StateError(f"revision conflict: expected {args.if_revision}, found {revision}")
        tasks = get_tasks(state, args.task)
        requested = status or args.status
        expected = from_status or args.from_status
        for task in tasks:
            if expected and task.status != expected:
                raise StateError(f"{task.task_id}: expected {expected}, found {task.status}")
            if not args.force and requested not in ALLOWED_TRANSITIONS[task.status]:
                raise StateError(f"invalid transition {task.task_id}: {task.status} -> {requested}")
            if requested == "blocked" and not args.reason:
                raise StateError("--reason is required for blocked status")
            if requested == "blocked" and not args.resume_from:
                raise StateError("--resume-from is required for blocked status")
        raw_tasks = data["tasks"]
        assert isinstance(raw_tasks, list)
        selected = set(args.task or [task.task_id for task in tasks])
        for item in raw_tasks:
            if not isinstance(item, dict) or item.get("id") not in selected:
                continue
            if requested == "completed" and args.acceptance is None and item.get("acceptance") is None:
                raise StateError(
                    f"{item.get('id')}: completion requires acceptance; provide --acceptance-json or --acceptance-note"
                )
            item["status"] = requested
            if requested == "blocked":
                item["blockedReason"] = args.reason
                item["resumeFrom"] = args.resume_from
            elif requested == "reviewed":
                item.pop("blockedReason", None)
                item.pop("resumeFrom", None)
            if args.acceptance is not None:
                item["acceptance"] = args.acceptance
        data["revision"] = int(revision) + 1
        data["updated_at"] = utc_now()
        if not args.dry_run:
            write_json_state(path, data)
            try:
                synced_todo = sync_markdown_view(path, data, args.todo)
            except (OSError, StateError) as error:
                # The canonical JSON has already been committed; surface view drift
                # without pretending the lifecycle write failed.
                sync_warning = f"todo.md sync failed: {error}"
    result = {
        "state": str(path),
        "updated": sorted(selected),
        "status": requested,
        "revision": data["revision"],
        "dry_run": args.dry_run,
    }
    if synced_todo is not None:
        result["todo"] = str(synced_todo)
        result["todo_synced"] = True
    if sync_warning:
        result["todo_synced"] = False
        result["warning"] = sync_warning
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


def command_query(args: argparse.Namespace) -> int:
    if backend_is_markdown(args):
        state = load_state(args.todo)
        errors = validate_state(
            state,
            check_files=not args.skip_file_check,
            check_plans=not args.skip_plan_check,
        )
    else:
        data, state = load_json_state(state_path_from_args(args))
        errors = validate_json_state(
            state.path,
            data,
            check_files=not args.skip_file_check,
            check_plans=not args.skip_plan_check,
        )
    if errors:
        raise StateError("state validation failed:\n- " + "\n- ".join(errors))
    print(format_tasks(get_tasks(state, args.task), state, args.format))
    return 0


def command_ready(args: argparse.Namespace) -> int:
    if backend_is_markdown(args):
        state = load_state(args.todo)
    else:
        data, state = load_json_state(state_path_from_args(args))
        errors = validate_json_state(state.path, data)
        if errors:
            raise StateError("state validation failed:\n- " + "\n- ".join(errors))
    print(format_tasks(ready_tasks(state), state, args.format))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    if backend_is_markdown(args):
        state = load_state(args.todo)
        errors = validate_state(
            state,
            check_files=not args.skip_file_check,
            check_plans=not args.skip_plan_check,
        )
        result = {
            "backend": "markdown-legacy",
            "todo": str(state.path),
            "task_count": len(state.tasks),
            "valid": not errors,
            "errors": errors,
        }
    else:
        path = state_path_from_args(args).resolve()
        try:
            data, state = load_json_state(path)
            errors = validate_json_state(
                path,
                data,
                check_files=not args.skip_file_check,
                check_plans=not args.skip_plan_check,
            )
            result = {
                "backend": "json",
                "state": str(path),
                "revision": data.get("revision"),
                "task_count": len(state.tasks),
                "valid": not errors,
                "errors": errors,
            }
        except StateError as error:
            result = {
                "backend": "json",
                "state": str(path),
                "valid": False,
                "errors": [str(error)],
            }
            errors = [str(error)]
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if not errors else 1


def command_lint_plan(args: argparse.Namespace) -> int:
    errors = lint_plan(args.plan, args.task, args.blocked_by)
    result = {
        "plan": str(args.plan),
        "valid": not errors,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if not errors else 1


def command_set_status(args: argparse.Namespace) -> int:
    if not backend_is_markdown(args):
        args.acceptance = parse_acceptance(args)
        return json_transition(args)
    if not args.legacy_write:
        raise StateError("legacy --todo is read-only; run import-md and update --state instead")
    state = load_state(args.todo)
    errors = validate_state(state)
    if errors:
        raise StateError("todo validation failed:\n- " + "\n- ".join(errors))

    tasks = get_tasks(state, args.task)
    requested = args.status
    for task in tasks:
        if args.from_status and task.status != args.from_status:
            raise StateError(
                f"{task.task_id}: expected {args.from_status}, found {task.status}"
            )
        if not args.force and requested not in ALLOWED_TRANSITIONS[task.status]:
            raise StateError(f"invalid transition {task.task_id}: {task.status} -> {requested}")

    lines = list(state.lines)
    for task in tasks:
        lines[task.row_index] = replace_status(lines[task.row_index], requested)
    new_text = "".join(lines)

    if not args.dry_run:
        atomic_write(state.path, new_text)

    result = {
        "todo": str(state.path),
        "updated": [task.task_id for task in tasks],
        "status": requested,
        "dry_run": args.dry_run,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


def parse_acceptance(args: argparse.Namespace) -> object | None:
    if args.acceptance_json:
        try:
            return json.loads(args.acceptance_json)
        except json.JSONDecodeError as error:
            raise StateError(f"--acceptance-json must contain valid JSON: {error}") from error
    return args.acceptance_note


def command_alias(args: argparse.Namespace) -> int:
    args.status = args.target_status
    args.from_status = args.expected_from
    args.reason = getattr(args, "reason", None)
    args.resume_from = getattr(args, "resume_from", None)
    args.acceptance = parse_acceptance(args)
    args.force = False
    args.dry_run = False
    return command_set_status(args)


def command_import_md(args: argparse.Namespace) -> int:
    state_path = state_path_from_args(args).resolve()
    todo_path = args.todo.expanduser().resolve()
    data = markdown_to_json(todo_path, check_plans=not args.skip_plan_check)
    errors = validate_json_state(state_path, data, check_plans=not args.skip_plan_check)
    if errors:
        raise StateError("import validation failed:\n- " + "\n- ".join(errors))
    with state_lock(state_path):
        if state_path.exists() and not args.force:
            raise StateError(f"state file already exists; use --force to replace: {state_path}")
        write_json_state(state_path, data)
    print(json.dumps({"state": str(state_path), "imported_from": str(todo_path), "revision": 0}, ensure_ascii=True, indent=2))
    return 0


def command_export_md(args: argparse.Namespace) -> int:
    state_path = state_path_from_args(args).resolve()
    todo_path = args.todo.expanduser().resolve()
    data, _ = load_json_state(state_path)
    errors = validate_json_state(state_path, data)
    if errors:
        raise StateError("state validation failed:\n- " + "\n- ".join(errors))
    rendered = render_markdown(data, state_path, todo_path)
    if args.check:
        current = todo_path.read_text(encoding="utf-8") if todo_path.is_file() else None
        normalized_rendered = rendered.replace("\r\n", "\n")
        result = {
            "state": str(state_path),
            "todo": str(todo_path),
            "revision": data.get("revision"),
            "in_sync": current == normalized_rendered,
        }
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if current == normalized_rendered else 1
    atomic_write(todo_path, rendered)
    print(json.dumps({"state": str(state_path), "todo": str(todo_path), "revision": data.get("revision"), "in_sync": True}, ensure_ascii=True, indent=2))
    return 0


def add_state_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--todo", type=Path)


def add_query_arguments(parser: argparse.ArgumentParser) -> None:
    add_state_arguments(parser)
    parser.add_argument("--task", nargs="*")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--skip-file-check", action="store_true")
    parser.add_argument("--skip-plan-check", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read, validate, query, and update the canonical JSON task state")
    subparsers = parser.add_subparsers(dest="command", required=True)

    query = subparsers.add_parser("query", help="show tasks")
    add_query_arguments(query)
    query.set_defaults(function=command_query)

    ready = subparsers.add_parser("ready", help="show currently executable tasks")
    add_state_arguments(ready)
    ready.add_argument("--format", choices=("table", "json"), default="table")
    ready.set_defaults(function=command_ready)

    validate = subparsers.add_parser(
        "validate", help="validate todo IDs, paths, statuses, DAG, and plan documents"
    )
    add_state_arguments(validate)
    validate.add_argument("--skip-file-check", action="store_true")
    validate.add_argument("--skip-plan-check", action="store_true")
    validate.set_defaults(function=command_validate)

    lint = subparsers.add_parser("lint-plan", help="validate one plan document")
    lint.add_argument("--plan", type=Path, required=True)
    lint.add_argument("--task")
    lint.add_argument("--blocked-by", nargs="*", default=None)
    lint.set_defaults(function=command_lint_plan)

    update = subparsers.add_parser("set-status", help="atomically update one status for many tasks")
    add_state_arguments(update)
    update.add_argument("--task", nargs="+", required=True)
    update.add_argument("--status", choices=STATUSES, required=True)
    update.add_argument("--from-status", choices=STATUSES)
    update.add_argument("--force", action="store_true")
    update.add_argument("--dry-run", action="store_true")
    update.add_argument("--if-revision", type=int)
    update.add_argument("--reason")
    update.add_argument("--resume-from")
    update.add_argument("--acceptance-json")
    update.add_argument("--acceptance-note")
    update.add_argument("--legacy-write", action="store_true")
    update.set_defaults(function=command_set_status)

    import_md = subparsers.add_parser("import-md", help="import legacy todo.md into canonical JSON state")
    import_md.add_argument("--todo", type=Path, required=True)
    import_md.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    import_md.add_argument("--force", action="store_true")
    import_md.add_argument("--skip-plan-check", action="store_true")
    import_md.set_defaults(function=command_import_md)

    export_md = subparsers.add_parser("export-md", help="render the canonical JSON state as todo.md")
    export_md.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    export_md.add_argument("--todo", type=Path, default=DEFAULT_TODO_VIEW)
    export_md.add_argument("--check", action="store_true")
    export_md.set_defaults(function=command_export_md)

    for name, target_status, expected_from in (
        ("review", "reviewed", "pending"),
        ("claim", "in_progress", "reviewed"),
        ("complete", "completed", "in_progress"),
        ("block", "blocked", None),
        ("resume", "reviewed", "blocked"),
    ):
        alias = subparsers.add_parser(name, help=f"transition task to {target_status}")
        alias.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
        alias.add_argument("--todo", type=Path)
        alias.add_argument("--task", nargs="+", required=True)
        alias.add_argument("--if-revision", type=int)
        alias.add_argument("--reason")
        alias.add_argument("--resume-from")
        alias.add_argument("--acceptance-json")
        alias.add_argument("--acceptance-note")
        alias.set_defaults(
            function=command_alias,
            target_status=target_status,
            expected_from=expected_from,
        )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.function(args)
    except StateError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
