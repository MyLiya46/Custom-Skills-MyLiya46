#!/usr/bin/env python3
"""Create and inspect plan-executor run state without touching canonical task state.

The file is intentionally independent from plan_state.py: todo.md owns task
lifecycle, while this tool records one subagent attempt and its checkpoints.
It does not kill processes or infer platform-level agent liveness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MODES = ("worker", "direct")
PROFILES = ("short", "normal", "long-infra", "external", "e2e", "unknown")
TERMINAL_STATUSES = {"completed", "completed_offline", "blocked", "blocked_external", "failed"}
SUCCESS_STATUSES = {"completed", "completed_offline"}
BLOCKED_STATUSES = {"blocked", "blocked_external", "failed"}
CHECKPOINT_STATUSES = {
    "acknowledged",
    "running",
    "waiting_external",
    "validating",
    "reconciling",
}
NONTERMINAL_STATUSES = {"created", *CHECKPOINT_STATUSES, "stale_candidate"}
EVENTS = {
    "phase_started",
    "phase_progress",
    "phase_completed",
    "phase_blocked",
    "reconciled",
    "resumed",
    "run_completed",
    "run_blocked",
    "run_failed",
}
PHASE_EVENTS = {
    "phase_started",
    "phase_progress",
    "phase_completed",
    "phase_blocked",
}
TASK_ID_RE = re.compile(r"^T\d+$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESUME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/ -]{0,127}$")
SAFE_PROJECT_RE = re.compile(r"[^A-Za-z0-9._-]+")
REQUIRED_STATE_KEYS = (
    "schema_version",
    "task_id",
    "run_id",
    "attempt",
    "mode",
    "profile",
    "status",
    "phase",
    "started_at",
    "updated_at",
    "last_progress_at",
    "current_command",
    "changed_files",
    "checkpoints",
    "phase_history",
    "result_path",
    "acceptance",
    "blocked_reason",
    "resume_from",
)
TERMINAL_EVENTS = {
    "completed": "run_completed",
    "completed_offline": "run_completed",
    "blocked": "run_blocked",
    "blocked_external": "run_blocked",
    "failed": "run_failed",
}


class RunStateError(Exception):
    """A user-correctable run state error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RunStateError(f"invalid timestamp: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def default_state_dir() -> Path:
    configured = os.environ.get("PLAN_EXECUTOR_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    project_name = SAFE_PROJECT_RE.sub("-", Path.cwd().name).strip("-") or "project"
    return Path(tempfile.gettempdir()) / "plan-executor-runs" / project_name


def state_dir(args: argparse.Namespace) -> Path:
    return Path(args.state_dir).expanduser() if args.state_dir else default_state_dir()


def validate_task_id(task_id: str) -> str:
    if not TASK_ID_RE.fullmatch(task_id):
        raise RunStateError(f"invalid task ID: {task_id}")
    return task_id


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise RunStateError(f"invalid run ID: {run_id}")
    return run_id


def resolve_state_file(args: argparse.Namespace) -> Path:
    if args.state_file:
        return Path(args.state_file).expanduser().resolve()
    if not args.task or not args.run_id:
        raise RunStateError("provide --state-file or both --task and --run-id")
    task_id = validate_task_id(args.task)
    run_id = validate_run_id(args.run_id)
    return (state_dir(args) / task_id / f"{run_id}.json").resolve()


def read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RunStateError(f"run state not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RunStateError(f"cannot read run state: {path}: {error}") from error
    if not isinstance(value, dict):
        raise RunStateError(f"run state must be a JSON object: {path}")
    return value


def validate_run_state(state: dict[str, Any], path: Path | None = None) -> list[str]:
    """Return structural and lifecycle errors without changing the state."""
    prefix = f"{path}: " if path else ""
    errors: list[str] = []
    missing = [key for key in REQUIRED_STATE_KEYS if key not in state]
    errors.extend(f"{prefix}missing field: {key}" for key in missing)

    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{prefix}unsupported schema_version: {state.get('schema_version')!r}")
    task_id = state.get("task_id")
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        errors.append(f"{prefix}invalid task_id")
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        errors.append(f"{prefix}invalid run_id")
    if state.get("mode") not in MODES:
        errors.append(f"{prefix}invalid mode: {state.get('mode')!r}")
    if state.get("profile") not in PROFILES:
        errors.append(f"{prefix}invalid profile: {state.get('profile')!r}")
    status = state.get("status")
    if status not in TERMINAL_STATUSES | NONTERMINAL_STATUSES:
        errors.append(f"{prefix}invalid status: {status!r}")
    if not isinstance(state.get("attempt"), int) or state.get("attempt", 0) < 1:
        errors.append(f"{prefix}attempt must be a positive integer")
    interval = state.get("checkpoint_interval_seconds")
    if interval is not None and (
        not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0
    ):
        errors.append(f"{prefix}checkpoint_interval_seconds must be a positive number")

    timestamps: dict[str, datetime] = {}
    for field in ("started_at", "updated_at", "last_progress_at", "finished_at"):
        if field not in state:
            continue
        value = state.get(field)
        if not isinstance(value, str):
            errors.append(f"{prefix}{field} must be an ISO timestamp")
            continue
        try:
            parsed = parse_timestamp(value)
        except RunStateError as error:
            errors.append(f"{prefix}{error}")
            continue
        if parsed is not None:
            timestamps[field] = parsed
    if "started_at" in timestamps and "updated_at" in timestamps and timestamps["updated_at"] < timestamps["started_at"]:
        errors.append(f"{prefix}updated_at precedes started_at")
    if "finished_at" in timestamps and "updated_at" in timestamps and timestamps["finished_at"] < timestamps["updated_at"]:
        errors.append(f"{prefix}finished_at precedes updated_at")

    if not isinstance(state.get("changed_files"), list) or not all(isinstance(item, str) for item in state.get("changed_files", [])):
        errors.append(f"{prefix}changed_files must be a list of strings")
    for optional_list in ("external_waits", "checkpoint_phases"):
        if optional_list in state and (
            not isinstance(state.get(optional_list), list)
            or not all(isinstance(item, str) for item in state.get(optional_list, []))
        ):
            errors.append(f"{prefix}{optional_list} must be a list of strings")
    checkpoints = state.get("checkpoints")
    if not isinstance(checkpoints, list):
        errors.append(f"{prefix}checkpoints must be a list")
        checkpoints = []
    phase_history = state.get("phase_history")
    if not isinstance(phase_history, list):
        errors.append(f"{prefix}phase_history must be a list")
        phase_history = []
    for index, checkpoint in enumerate(checkpoints, 1):
        if not isinstance(checkpoint, dict):
            errors.append(f"{prefix}checkpoint {index} must be an object")
            continue
        if checkpoint.get("seq") != index:
            errors.append(f"{prefix}checkpoint {index} has invalid seq")
        if checkpoint.get("event") not in EVENTS:
            errors.append(f"{prefix}checkpoint {index} has invalid event")
        if checkpoint.get("status") not in CHECKPOINT_STATUSES | TERMINAL_STATUSES:
            errors.append(f"{prefix}checkpoint {index} has invalid status")
        if not isinstance(checkpoint.get("phase"), str) or not checkpoint.get("phase"):
            errors.append(f"{prefix}checkpoint {index} has empty phase")
        try:
            if not isinstance(checkpoint.get("at"), str):
                raise RunStateError("checkpoint at must be an ISO timestamp")
            parse_timestamp(checkpoint.get("at"))
        except RunStateError as error:
            errors.append(f"{prefix}checkpoint {index}: {error}")
    for index, history in enumerate(phase_history, 1):
        if not isinstance(history, dict) or history.get("seq") != index:
            errors.append(f"{prefix}phase_history {index} has invalid seq or shape")

    resume_from = state.get("resume_from")
    if resume_from is not None:
        if not isinstance(resume_from, str) or not RESUME_RE.fullmatch(resume_from) or resume_from.lower() == "terminal":
            errors.append(f"{prefix}invalid resume_from boundary")
        else:
            known_boundaries = {state.get("phase"), "resume"}
            declared_phases = state.get("checkpoint_phases", [])
            if isinstance(declared_phases, list):
                known_boundaries.update(item for item in declared_phases if isinstance(item, str))
            for checkpoint in checkpoints:
                if isinstance(checkpoint, dict):
                    known_boundaries.update(
                        value for value in (checkpoint.get("phase"), checkpoint.get("next_checkpoint")) if value
                    )
            if resume_from not in known_boundaries:
                errors.append(f"{prefix}resume_from is outside known checkpoint boundaries: {resume_from}")
    if status in SUCCESS_STATUSES and resume_from:
        errors.append(f"{prefix}successful run cannot have resume_from")
    if status in BLOCKED_STATUSES and not isinstance(state.get("blocked_reason"), str):
        errors.append(f"{prefix}{status} run requires blocked_reason")
    if status in BLOCKED_STATUSES and not str(state.get("blocked_reason") or "").strip():
        errors.append(f"{prefix}{status} run requires a non-empty blocked_reason")
    if status in BLOCKED_STATUSES and not resume_from:
        errors.append(f"{prefix}{status} run requires resume_from")
    if status in TERMINAL_STATUSES:
        if "finished_at" not in state:
            errors.append(f"{prefix}terminal run requires finished_at")
        if state.get("phase") != "terminal":
            errors.append(f"{prefix}terminal run must have phase=terminal")
        if not checkpoints or checkpoints[-1].get("event") != TERMINAL_EVENTS[status]:
            errors.append(f"{prefix}terminal run must end with {TERMINAL_EVENTS[status]}")
        if checkpoints and checkpoints[-1].get("status") != status:
            errors.append(f"{prefix}terminal checkpoint status must match run status")
        if status in SUCCESS_STATUSES and state.get("acceptance") is None:
            errors.append(f"{prefix}{status} run requires acceptance")
    else:
        if "finished_at" in state:
            errors.append(f"{prefix}non-terminal run cannot have finished_at")
        if checkpoints and checkpoints[-1].get("event") in TERMINAL_EVENTS.values():
            errors.append(f"{prefix}non-terminal run cannot end with a terminal event")
    return sorted(set(errors))


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
        os.replace(temporary_name, path)
        temporary_name = None
    except OSError as error:
        raise RunStateError(f"cannot write run state: {path}: {error}") from error
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2))


def merge_unique(existing: list[str] | None, additions: list[str]) -> list[str]:
    result: list[str] = []
    for item in [*(existing or []), *additions]:
        if item and item not in result:
            result.append(item)
    return result


def append_checkpoint(
    state: dict[str, Any],
    *,
    status: str,
    phase: str,
    current_command: str,
    changed_files: list[str],
    message: str,
    next_checkpoint: str,
    event: str,
    at: str,
) -> None:
    if event not in EVENTS:
        raise RunStateError(f"invalid run event: {event}")
    checkpoints = state.setdefault("checkpoints", [])
    if not isinstance(checkpoints, list):
        raise RunStateError("run state checkpoints must be a list")
    checkpoint = {
        "seq": len(checkpoints) + 1,
        "at": at,
        "event": event,
        "status": status,
        "phase": phase,
        "current_command": current_command,
        "changed_files": changed_files,
        "message": message,
        "next_checkpoint": next_checkpoint,
    }
    checkpoints.append(checkpoint)
    phase_history = state.setdefault("phase_history", [])
    if not isinstance(phase_history, list):
        raise RunStateError("run state phase_history must be a list")
    phase_history.append(
        {
            "seq": len(phase_history) + 1,
            "at": at,
            "event": event,
            "phase": phase,
            "status": status,
            "message": message,
            "next_checkpoint": next_checkpoint,
        }
    )


def parse_json_option(raw: str | None, option_name: str) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise RunStateError(f"{option_name} must contain valid JSON: {error}") from error


def read_acceptance(args: argparse.Namespace) -> Any:
    if args.acceptance_json and args.acceptance_file:
        raise RunStateError("use only one of --acceptance-json and --acceptance-file")
    if args.acceptance_json:
        return parse_json_option(args.acceptance_json, "--acceptance-json")
    if args.acceptance_file:
        path = Path(args.acceptance_file).expanduser()
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise RunStateError(f"cannot read acceptance file: {path}: {error}") from error
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return args.acceptance_note


def active_state_files(task_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not task_dir.is_dir():
        return []
    active: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(task_dir.glob("*.json")):
        try:
            value = read_state(path)
        except RunStateError:
            continue
        if value.get("status") not in TERMINAL_STATUSES:
            active.append((path, value))
    return active


def state_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob("T*/*.json"))


def scan_states(root: Path) -> tuple[list[dict[str, Any]], list[str], dict[str, list[Path]]]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    active_by_task: dict[str, list[Path]] = {}
    for path in state_files(root):
        try:
            state = read_state(path)
        except RunStateError as error:
            errors.append(str(error))
            entries.append({"state_file": str(path.resolve()), "valid": False, "errors": [str(error)]})
            continue
        state_errors = validate_run_state(state, path)
        state["state_file"] = str(path.resolve())
        state["valid"] = not state_errors
        state["errors"] = state_errors
        entries.append(state)
        task_id = state.get("task_id")
        if isinstance(task_id, str) and state.get("status") not in TERMINAL_STATUSES:
            active_by_task.setdefault(task_id, []).append(path)
        errors.extend(state_errors)
    for task_id, paths in active_by_task.items():
        if len(paths) > 1:
            errors.append(
                f"{task_id}: duplicate active runs: {', '.join(str(path.resolve()) for path in paths)}"
            )
    return entries, sorted(set(errors)), active_by_task


def plan_run_warnings(entries: list[dict[str, Any]], plan_path: Path) -> list[str]:
    """Report lifecycle/run-state drift without turning historical evidence invalid."""
    try:
        with plan_path.open("r", encoding="utf-8") as stream:
            plan = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"cannot read plan state for consistency check: {plan_path}: {error}"]
    raw_tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(raw_tasks, list):
        return [f"plan state for consistency check has no tasks list: {plan_path}"]
    by_task: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if isinstance(task_id, str):
            by_task.setdefault(task_id, []).append(entry)
    warnings: list[str] = []
    for item in raw_tasks:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        task_id = item["id"]
        lifecycle = item.get("status")
        runs = by_task.get(task_id, [])
        active = [run for run in runs if run.get("status") not in TERMINAL_STATUSES]
        successful = [run for run in runs if run.get("status") in SUCCESS_STATUSES]
        blocked = [run for run in runs if run.get("status") in BLOCKED_STATUSES]
        if lifecycle == "completed" and (active or (runs and not successful)):
            warnings.append(f"{task_id}: plan status completed but run state has no successful terminal result")
        elif lifecycle == "blocked" and (active or successful):
            warnings.append(f"{task_id}: plan status blocked conflicts with active/successful run state")
        elif lifecycle == "in_progress" and not active:
            warnings.append(f"{task_id}: plan status in_progress has no active run state")
        elif lifecycle in {"reviewed", "pending"} and active:
            warnings.append(f"{task_id}: plan status {lifecycle} conflicts with active run state")
        if lifecycle == "completed" and blocked:
            warnings.append(f"{task_id}: plan status completed retains blocked/failed run evidence")
    return sorted(set(warnings))


def repair_state(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Repair only fields that can be derived without discarding run evidence."""
    repaired = dict(state)
    changes: list[str] = []
    defaults: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "attempt": 1,
        "mode": "worker",
        "profile": "unknown",
        "phase": "dispatch",
        "current_command": "",
        "changed_files": [],
        "checkpoints": [],
        "phase_history": [],
        "result_path": None,
        "acceptance": None,
        "blocked_reason": None,
        "resume_from": None,
        "checkpoint_interval_seconds": None,
    }
    for key, value in defaults.items():
        if key not in repaired:
            repaired[key] = value
            changes.append(f"added {key}")

    if repaired.get("status") in TERMINAL_STATUSES:
        status = repaired.get("status")
        if status in SUCCESS_STATUSES and repaired.get("resume_from"):
            repaired["resume_from"] = None
            changes.append("cleared resume_from on successful run")
        if repaired.get("phase") != "terminal":
            repaired["phase"] = "terminal"
            changes.append("set phase=terminal")
        if not repaired.get("finished_at"):
            repaired["finished_at"] = repaired.get("updated_at") or repaired.get("started_at") or utc_now()
            changes.append("derived finished_at")
        if status in BLOCKED_STATUSES and not repaired.get("blocked_reason"):
            checkpoints = repaired.get("checkpoints") or []
            message = checkpoints[-1].get("message") if isinstance(checkpoints[-1], dict) else None
            repaired["blocked_reason"] = message or "repaired terminal run requires follow-up"
            changes.append("derived blocked_reason")
        if status in BLOCKED_STATUSES and not repaired.get("resume_from"):
            repaired["resume_from"] = "resume"
            changes.append("derived resume_from")
        checkpoints = repaired.get("checkpoints")
        terminal_event = TERMINAL_EVENTS.get(status)
        if isinstance(checkpoints, list) and terminal_event and (
            not checkpoints or not isinstance(checkpoints[-1], dict) or checkpoints[-1].get("event") != terminal_event
        ):
            at = repaired.get("finished_at") or utc_now()
            checkpoint = {
                "seq": len(checkpoints) + 1,
                "at": at,
                "event": terminal_event,
                "status": status,
                "phase": "terminal",
                "current_command": str(repaired.get("current_command") or ""),
                "changed_files": list(repaired.get("changed_files") or []),
                "message": "repaired terminal consistency",
                "next_checkpoint": "",
            }
            checkpoints.append(checkpoint)
            history = repaired.setdefault("phase_history", [])
            if isinstance(history, list):
                history.append(
                    {
                        "seq": len(history) + 1,
                        "at": at,
                        "event": terminal_event,
                        "phase": "terminal",
                        "status": status,
                        "message": "repaired terminal consistency",
                        "next_checkpoint": "",
                    }
                )
            changes.append(f"appended {terminal_event} checkpoint")
    else:
        if repaired.get("resume_from") is None and repaired.get("status") in {"running", "waiting_external", "validating", "reconciling"}:
            checkpoints = repaired.get("checkpoints") or []
            candidate = checkpoints[-1].get("next_checkpoint") if checkpoints and isinstance(checkpoints[-1], dict) else None
            repaired["resume_from"] = candidate or repaired.get("phase") or "resume"
            changes.append("derived resume_from")
    return repaired, changes


def command_init(args: argparse.Namespace) -> int:
    task_id = validate_task_id(args.task)
    mode = args.mode
    if mode not in MODES:
        raise RunStateError(f"invalid execution mode: {mode}")
    profile = args.profile
    if profile not in PROFILES:
        raise RunStateError(f"invalid profile: {profile}")

    root = state_dir(args).resolve()
    task_dir = root / task_id
    unreadable = [path for path in task_dir.glob("*.json") if path.is_file()]
    for path in unreadable:
        try:
            read_state(path)
        except RunStateError as error:
            raise RunStateError(f"cannot create run while state is unreadable: {error}") from error
    active = active_state_files(task_dir)
    if active:
        paths = ", ".join(str(path) for path, _ in active)
        raise RunStateError(f"active run already exists for {task_id}: {paths}")

    run_id = validate_run_id(args.run_id) if args.run_id else uuid.uuid4().hex[:12]
    path = (task_dir / f"{run_id}.json").resolve()
    if path.exists():
        raise RunStateError(f"run state already exists: {path}")

    timestamp = utc_now()
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "run_id": run_id,
        "attempt": 1,
        "mode": mode,
        "profile": profile,
        "status": "created",
        "phase": "dispatch",
        "started_at": timestamp,
        "updated_at": timestamp,
        "last_progress_at": timestamp,
        "current_command": "",
        "changed_files": [],
        "checkpoints": [],
        "phase_history": [],
        "result_path": None,
        "acceptance": None,
        "blocked_reason": None,
        "resume_from": args.resume_from,
        "expected_duration": args.expected_duration,
        "checkpoint_interval_seconds": args.checkpoint_interval,
        "external_waits": args.external_waits,
        "checkpoint_phases": args.checkpoint_phases,
    }
    atomic_write_json(path, state)
    state["state_file"] = str(path)
    print_json(state)
    return 0


def command_checkpoint(args: argparse.Namespace) -> int:
    path = resolve_state_file(args)
    state = read_state(path)
    if state.get("status") in TERMINAL_STATUSES:
        raise RunStateError("cannot checkpoint a terminal run")

    status = args.status or ("acknowledged" if state.get("status") == "created" else "running")
    if status not in CHECKPOINT_STATUSES:
        raise RunStateError(f"invalid checkpoint status: {status}")
    event = args.event or ("phase_started" if status == "acknowledged" else "phase_progress")
    if event not in PHASE_EVENTS:
        raise RunStateError(f"invalid run event: {event}")
    timestamp = utc_now()
    phase = args.phase or str(state.get("phase") or "work")
    command = args.current_command if args.current_command is not None else str(
        state.get("current_command") or ""
    )
    changed_files = merge_unique(state.get("changed_files"), args.changed_file)
    append_checkpoint(
        state,
        status=status,
        phase=phase,
        current_command=command,
        changed_files=changed_files,
        message=args.message,
        next_checkpoint=args.next_checkpoint,
        event=event,
        at=timestamp,
    )
    state.update(
        {
            "status": status,
            "phase": phase,
            "current_command": command,
            "changed_files": changed_files,
            "updated_at": timestamp,
            "last_progress_at": timestamp,
        }
    )
    if event == "phase_completed" and args.next_checkpoint:
        state["resume_from"] = args.next_checkpoint
    if args.pid is not None:
        state["pid"] = args.pid
    atomic_write_json(path, state)
    state["state_file"] = str(path)
    print_json(state)
    return 0


def command_reconcile(args: argparse.Namespace) -> int:
    path = resolve_state_file(args)
    state = read_state(path)
    if state.get("status") in TERMINAL_STATUSES:
        raise RunStateError("cannot reconcile a terminal run")
    timestamp = utc_now()
    phase = "reconcile"
    append_checkpoint(
        state,
        status="reconciling",
        phase=phase,
        current_command=str(state.get("current_command") or ""),
        changed_files=list(state.get("changed_files") or []),
        message=args.reason,
        next_checkpoint=args.next_checkpoint,
        event="reconciled",
        at=timestamp,
    )
    state.update(
        {
            "status": "reconciling",
            "phase": phase,
            "updated_at": timestamp,
            "last_progress_at": timestamp,
        }
    )
    atomic_write_json(path, state)
    state["state_file"] = str(path)
    print_json(state)
    return 0


def command_resume(args: argparse.Namespace) -> int:
    path = resolve_state_file(args)
    state = read_state(path)
    previous_status = state.get("status")
    if state.get("status") in SUCCESS_STATUSES:
        raise RunStateError("cannot resume a successful run")
    if previous_status in BLOCKED_STATUSES and not args.allow_terminal_resume:
        raise RunStateError(
            "resuming a terminal blocked/failed run requires --allow-terminal-resume"
        )
    timestamp = utc_now()
    phase = args.phase or str(state.get("resume_from") or "resume")
    if not RESUME_RE.fullmatch(phase) or phase.lower() == "terminal":
        raise RunStateError(f"invalid resume phase: {phase}")
    reason = args.reason or "resume from existing run state"
    changed_files = list(state.get("changed_files") or [])
    append_checkpoint(
        state,
        status="running",
        phase=phase,
        current_command=str(state.get("current_command") or ""),
        changed_files=changed_files,
        message=reason,
        next_checkpoint=args.next_checkpoint,
        event="resumed",
        at=timestamp,
    )
    state.update(
        {
            "attempt": int(state.get("attempt", 1)) + 1,
            "status": "running",
            "phase": phase,
            "updated_at": timestamp,
            "last_progress_at": timestamp,
            "resume_from": phase,
        }
    )
    if previous_status in BLOCKED_STATUSES:
        state.pop("finished_at", None)
        state["blocked_reason"] = None
    atomic_write_json(path, state)
    state["state_file"] = str(path)
    print_json(state)
    return 0


def command_finish(args: argparse.Namespace) -> int:
    path = resolve_state_file(args)
    state = read_state(path)
    if state.get("status") in TERMINAL_STATUSES:
        raise RunStateError("run is already terminal")
    if args.status in BLOCKED_STATUSES and not args.reason:
        raise RunStateError(f"--reason is required for status {args.status}")
    if args.status in BLOCKED_STATUSES and not args.resume_from:
        raise RunStateError(f"--resume-from is required for status {args.status}")

    timestamp = utc_now()
    changed_files = merge_unique(state.get("changed_files"), args.changed_file)
    supplied_acceptance = read_acceptance(args)
    acceptance = supplied_acceptance if supplied_acceptance is not None else state.get("acceptance")
    if args.status in SUCCESS_STATUSES and acceptance is None:
        raise RunStateError(f"status {args.status} requires acceptance; provide --acceptance-json, --acceptance-file, or --acceptance-note")
    append_checkpoint(
        state,
        status=args.status,
        phase="terminal",
        current_command=str(state.get("current_command") or ""),
        changed_files=changed_files,
        message=args.message,
        next_checkpoint="",
        event=TERMINAL_EVENTS[args.status],
        at=timestamp,
    )
    state.update(
        {
            "status": args.status,
            "phase": "terminal",
            "changed_files": changed_files,
            "updated_at": timestamp,
            "last_progress_at": timestamp,
            "finished_at": timestamp,
            "result_path": args.result_path if args.result_path is not None else state.get("result_path"),
            "acceptance": acceptance,
            "blocked_reason": args.reason if args.status in BLOCKED_STATUSES else None,
            "resume_from": (args.resume_from or state.get("resume_from"))
            if args.status in BLOCKED_STATUSES
            else None,
        }
    )
    atomic_write_json(path, state)
    state["state_file"] = str(path)
    print_json(state)
    return 0


def pid_alive(pid: Any) -> bool | None:
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def summarize_state(state: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Return a compact, stable view suitable for coordinator polling."""
    checkpoints = state.get("checkpoints")
    last_checkpoint = checkpoints[-1] if isinstance(checkpoints, list) and checkpoints else {}
    if not isinstance(last_checkpoint, dict):
        last_checkpoint = {}
    interval = state.get("checkpoint_interval_seconds")
    try:
        last_progress = parse_timestamp(state.get("last_progress_at"))
    except RunStateError:
        last_progress = None
    age: float | None = None
    if last_progress is not None:
        age = max(0.0, (datetime.now(timezone.utc) - last_progress).total_seconds())
    return {
        "state_file": str(path.resolve()) if path else state.get("state_file"),
        "task_id": state.get("task_id"),
        "run_id": state.get("run_id"),
        "attempt": state.get("attempt"),
        "mode": state.get("mode"),
        "profile": state.get("profile"),
        "status": state.get("status"),
        "phase": state.get("phase"),
        "updated_at": state.get("updated_at"),
        "last_progress_at": state.get("last_progress_at"),
        "idle_seconds": age,
        "checkpoint_interval_seconds": interval,
        "checkpoint_due": bool(
            state.get("status") not in TERMINAL_STATUSES
            and isinstance(interval, (int, float))
            and age is not None
            and age >= interval
        ),
        "current_command": state.get("current_command") or "",
        "changed_files_count": len(state.get("changed_files", []))
        if isinstance(state.get("changed_files"), list)
        else 0,
        "checkpoint_count": len(state.get("checkpoints", []))
        if isinstance(state.get("checkpoints"), list)
        else 0,
        "last_event": last_checkpoint.get("event"),
        "last_message": last_checkpoint.get("message", ""),
        "acceptance_recorded": state.get("acceptance") is not None,
        "blocked_reason": state.get("blocked_reason"),
        "resume_from": state.get("resume_from"),
    }


def command_inspect(args: argparse.Namespace) -> int:
    path = resolve_state_file(args)
    state = read_state(path)
    current = datetime.now(timezone.utc)
    last_progress = parse_timestamp(state.get("last_progress_at"))
    idle_seconds: float | None = None
    if last_progress is not None:
        idle_seconds = max(0.0, (current - last_progress).total_seconds())
    threshold = args.idle_timeout
    state["state_file"] = str(path)
    state["observation"] = {
        "terminal": state.get("status") in TERMINAL_STATUSES,
        "idle_seconds": idle_seconds,
        "idle_timeout_seconds": threshold,
        "idle_exceeded": bool(
            threshold is not None and idle_seconds is not None and idle_seconds >= threshold
        ),
        "active_command_present": bool(state.get("current_command")),
        "pid_alive": pid_alive(state.get("pid")),
        "checkpoint_due": bool(
            state.get("status") not in TERMINAL_STATUSES
            and isinstance(state.get("checkpoint_interval_seconds"), (int, float))
            and idle_seconds is not None
            and idle_seconds >= state["checkpoint_interval_seconds"]
        ),
    }
    if args.summary:
        result = summarize_state(state, path)
        result["observation"] = state["observation"]
        print_json(result)
    else:
        print_json(state)
    return 0


def command_list(args: argparse.Namespace) -> int:
    root = state_dir(args).resolve()
    entries: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.glob("T*/*.json")):
            try:
                value = read_state(path)
            except RunStateError:
                continue
            value["state_file"] = str(path.resolve())
            entries.append(summarize_state(value, path) if args.summary else value)
    print_json(entries)
    return 0


def command_summary(args: argparse.Namespace) -> int:
    path = resolve_state_file(args)
    state = read_state(path)
    print_json(summarize_state(state, path))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    consistency_warnings: list[str] = []
    if args.state_file:
        path = Path(args.state_file).expanduser().resolve()
        try:
            state = read_state(path)
        except RunStateError as error:
            result = {"valid": False, "state_file": str(path), "errors": [str(error)]}
            print_json(result)
            return 1
        errors = validate_run_state(state, path)
        result = {"valid": not errors, "state_file": str(path), "errors": errors}
        if args.summary:
            result["summary"] = summarize_state(state, path)
        if args.plan_state:
            consistency_warnings = plan_run_warnings([state], args.plan_state.expanduser().resolve())
            result["warnings"] = consistency_warnings
        print_json(result)
        return 0 if not errors else 1

    root = state_dir(args).resolve()
    entries, errors, active_by_task = scan_states(root)
    if args.plan_state:
        consistency_warnings = plan_run_warnings(entries, args.plan_state.expanduser().resolve())
    result = {
        "valid": not errors,
        "state_dir": str(root),
        "file_count": len(entries),
        "active_tasks": {task: [str(path.resolve()) for path in paths] for task, paths in active_by_task.items()},
        "errors": errors,
        "warnings": consistency_warnings,
        "states": [summarize_state(item, Path(item["state_file"])) for item in entries]
        if args.summary
        else entries,
    }
    print_json(result)
    return 0 if not errors else 1


def command_repair(args: argparse.Namespace) -> int:
    if args.state_file:
        paths = [Path(args.state_file).expanduser().resolve()]
        root = paths[0].parent
    else:
        root = state_dir(args).resolve()
        paths = state_files(root)

    loaded: list[tuple[Path, dict[str, Any]]] = []
    errors: list[str] = []
    for path in paths:
        try:
            loaded.append((path, read_state(path)))
        except RunStateError as error:
            errors.append(str(error))

    active_by_task: dict[str, list[Path]] = {}
    for path, state in loaded:
        task_id = state.get("task_id")
        if isinstance(task_id, str) and state.get("status") not in TERMINAL_STATUSES:
            active_by_task.setdefault(task_id, []).append(path)
    duplicate_tasks = {task for task, task_paths in active_by_task.items() if len(task_paths) > 1}
    for task in sorted(duplicate_tasks):
        errors.append(f"{task}: duplicate active runs; repair requires explicit run selection")

    changes: list[dict[str, Any]] = []
    for path, state in loaded:
        task_id = state.get("task_id")
        if task_id in duplicate_tasks and not args.state_file:
            continue
        repaired, state_changes = repair_state(state)
        state_errors = validate_run_state(repaired, path)
        if state_changes and not args.dry_run and not state_errors:
            atomic_write_json(path, repaired)
        changes.append(
            {
                "state_file": str(path),
                "changed": bool(state_changes),
                "changes": state_changes,
                "valid_after_repair": not state_errors,
                "errors_after_repair": state_errors,
            }
        )
        errors.extend(state_errors)

    result = {
        "valid": not errors,
        "state_dir": str(root),
        "dry_run": args.dry_run,
        "changes": changes,
        "errors": sorted(set(errors)),
    }
    print_json(result)
    return 0 if not errors else 1


def add_locator_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--run-id")


def add_phase_event_arguments(parser: argparse.ArgumentParser) -> None:
    add_locator_arguments(parser)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--current-command")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--message", default="")
    parser.add_argument("--next-checkpoint", default="")
    parser.add_argument("--pid", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and inspect plan-executor run state without changing docs/todo.json"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create one run state")
    init.add_argument("--state-dir", type=Path)
    init.add_argument("--task", required=True)
    init.add_argument("--run-id")
    init.add_argument("--mode", choices=MODES, default="worker")
    init.add_argument("--profile", choices=PROFILES, default="unknown")
    init.add_argument("--expected-duration")
    init.add_argument(
        "--checkpoint-interval",
        "--max-checkpoint-interval",
        dest="checkpoint_interval",
        type=float,
        help="maximum seconds between progress checkpoints",
    )
    init.add_argument("--external-wait", dest="external_waits", action="append", default=[])
    init.add_argument("--checkpoint-phase", dest="checkpoint_phases", action="append", default=[])
    init.add_argument("--resume-from")
    init.set_defaults(function=command_init)

    checkpoint = subparsers.add_parser("checkpoint", help="record a progress checkpoint")
    add_locator_arguments(checkpoint)
    checkpoint.add_argument("--status", choices=sorted(CHECKPOINT_STATUSES))
    checkpoint.add_argument("--event", choices=sorted(PHASE_EVENTS))
    checkpoint.add_argument("--phase")
    checkpoint.add_argument("--current-command")
    checkpoint.add_argument("--changed-file", action="append", default=[])
    checkpoint.add_argument("--message", default="")
    checkpoint.add_argument("--next-checkpoint", default="")
    checkpoint.add_argument("--pid", type=int)
    checkpoint.set_defaults(function=command_checkpoint)

    phase_start = subparsers.add_parser("phase-start", help="record a phase start event")
    add_phase_event_arguments(phase_start)
    phase_start.set_defaults(status="running", event="phase_started", function=command_checkpoint)

    phase_complete = subparsers.add_parser("phase-complete", help="record a phase completion event")
    add_phase_event_arguments(phase_complete)
    phase_complete.set_defaults(status="running", event="phase_completed", function=command_checkpoint)

    reconcile = subparsers.add_parser("reconcile", help="record a stale-run coordination check")
    add_locator_arguments(reconcile)
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--next-checkpoint", default="")
    reconcile.set_defaults(function=command_reconcile)

    resume = subparsers.add_parser("resume", help="resume an existing run without creating a new run_id")
    add_locator_arguments(resume)
    resume.add_argument("--reason")
    resume.add_argument("--phase")
    resume.add_argument("--next-checkpoint", default="")
    resume.add_argument(
        "--allow-terminal-resume",
        action="store_true",
        help="explicitly authorize recovery from blocked_external/blocked/failed",
    )
    resume.set_defaults(function=command_resume)

    summary = subparsers.add_parser("summary", help="show one compact coordinator summary")
    add_locator_arguments(summary)
    summary.set_defaults(function=command_summary)

    finish = subparsers.add_parser("finish", help="record a terminal result")
    add_locator_arguments(finish)
    finish.add_argument("--status", choices=sorted(TERMINAL_STATUSES), required=True)
    finish.add_argument("--reason")
    finish.add_argument("--message", default="")
    finish.add_argument("--changed-file", action="append", default=[])
    finish.add_argument("--result-path")
    finish.add_argument("--acceptance-json")
    finish.add_argument("--acceptance-file")
    finish.add_argument("--acceptance-note")
    finish.add_argument("--resume-from")
    finish.set_defaults(function=command_finish)

    inspect = subparsers.add_parser("inspect", help="observe a run without changing it")
    add_locator_arguments(inspect)
    inspect.add_argument("--idle-timeout", type=float)
    inspect.add_argument("--summary", action="store_true", help="return only the compact summary")
    inspect.set_defaults(function=command_inspect)

    listing = subparsers.add_parser("list", help="list run states in a state directory")
    listing.add_argument("--state-dir", type=Path)
    listing.add_argument("--summary", action="store_true", help="return compact summaries")
    listing.set_defaults(function=command_list)

    validate = subparsers.add_parser("validate", help="validate one run or all runs in a state directory")
    validate.add_argument("--state-dir", type=Path)
    validate.add_argument("--state-file", type=Path)
    validate.add_argument("--plan-state", type=Path, help="warn when todo lifecycle and run states disagree")
    validate.add_argument("--summary", action="store_true", help="omit full state payloads")
    validate.set_defaults(function=command_validate)

    repair = subparsers.add_parser("repair", help="repair derivable run-state fields atomically")
    repair.add_argument("--state-dir", type=Path)
    repair.add_argument("--state-file", type=Path)
    repair.add_argument("--dry-run", action="store_true")
    repair.set_defaults(function=command_repair)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.function(args)
    except (RunStateError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
