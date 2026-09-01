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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


STATUSES = ("pending", "reviewed", "in_progress", "completed", "blocked")
STATUS_RE = re.compile(r"\b(?:pending|reviewed|in_progress|completed|blocked)\b")
TASK_ROW_RE = re.compile(r"^\|\s*\d+\s*\|\s*T\d+\s*\|")
TASK_ID_RE = re.compile(r"^T\d+$")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

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


def plan_path(state: TodoState, task: Task) -> Path:
    if not task.plan_link:
        raise StateError(f"{task.task_id}: plan link is missing")
    return (state.path.parent / task.plan_link.replace("/", os.sep)).resolve()


def validate_state(state: TodoState, check_files: bool = True) -> list[str]:
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
        elif check_files and not plan_path(state, task).is_file():
            errors.append(f"{task.task_id}: plan file not found: {task.plan_link}")

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


def command_query(args: argparse.Namespace) -> int:
    state = load_state(args.todo)
    errors = validate_state(state, check_files=not args.skip_file_check)
    if errors:
        raise StateError("todo validation failed:\n- " + "\n- ".join(errors))
    print(format_tasks(get_tasks(state, args.task), state, args.format))
    return 0


def command_ready(args: argparse.Namespace) -> int:
    state = load_state(args.todo)
    print(format_tasks(ready_tasks(state), state, args.format))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    state = load_state(args.todo)
    errors = validate_state(state, check_files=not args.skip_file_check)
    result = {
        "todo": str(state.path),
        "task_count": len(state.tasks),
        "valid": not errors,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if not errors else 1


def command_set_status(args: argparse.Namespace) -> int:
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


def add_todo_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--todo", type=Path, default=Path("docs/todo.md"))


def add_query_arguments(parser: argparse.ArgumentParser) -> None:
    add_todo_argument(parser)
    parser.add_argument("--task", nargs="*")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--skip-file-check", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read, validate, query, and update docs/todo.md")
    subparsers = parser.add_subparsers(dest="command", required=True)

    query = subparsers.add_parser("query", help="show tasks")
    add_query_arguments(query)
    query.set_defaults(function=command_query)

    ready = subparsers.add_parser("ready", help="show currently executable tasks")
    add_todo_argument(ready)
    ready.add_argument("--format", choices=("table", "json"), default="table")
    ready.set_defaults(function=command_ready)

    validate = subparsers.add_parser("validate", help="validate IDs, paths, statuses, and DAG")
    add_todo_argument(validate)
    validate.add_argument("--skip-file-check", action="store_true")
    validate.set_defaults(function=command_validate)

    update = subparsers.add_parser("set-status", help="atomically update one status for many tasks")
    add_todo_argument(update)
    update.add_argument("--task", nargs="+", required=True)
    update.add_argument("--status", choices=STATUSES, required=True)
    update.add_argument("--from-status", choices=STATUSES)
    update.add_argument("--force", action="store_true")
    update.add_argument("--dry-run", action="store_true")
    update.set_defaults(function=command_set_status)
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
