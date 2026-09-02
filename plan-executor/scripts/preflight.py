#!/usr/bin/env python3
"""Run a compact, structured preflight for plan-executor tasks.

The command reports optional environment gaps as warnings and only fails for
requirements explicitly declared by the plan or CLI. It does not start,
restart, or mutate services.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Check:
    name: str
    status: str
    detail: str
    required: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "required": self.required,
        }


def split_values(value: str) -> list[str]:
    if not value or value.strip() in {"无", "none", "None", "N/A", "n/a"}:
        return []
    return [item.strip().strip("`") for item in re.split(r"[,，、;；\s]+", value) if item.strip()]


def plan_requirements(plan_path: Path) -> dict[str, list[str]]:
    text = plan_path.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^##\s+环境预检\s*$\n(.*?)(?=^##\s+|\Z)", text)
    if not match:
        return {
            "shells": [],
            "tools": [],
            "ports": [],
            "urls": [],
            "imports": [],
            "containers": [],
            "container_tools": [],
            "container_imports": [],
        }
    body = match.group(1)

    def field(label: str) -> str:
        found = re.search(rf"(?m)^\s*(?:[-*]\s*)?{re.escape(label)}\s*[：:]\s*(.*?)\s*$", body)
        return found.group(1) if found else ""

    ports = [str(int(value)) for value in re.findall(r"\b\d{2,5}\b", field("必需端口"))]
    return {
        "shells": split_values(field("必需 Shell")),
        "tools": split_values(field("必需命令")),
        "ports": ports,
        "urls": split_values(field("必需 URL")),
        "imports": split_values(field("必需 Python 模块")),
        "containers": split_values(field("必需 Docker 容器")),
        "container_tools": split_values(field("容器内必需命令")),
        "container_imports": split_values(field("容器内必需 Python 模块")),
    }


def run_version(command: str, timeout: float) -> str:
    try:
        completed = subprocess.run(
            [command, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0][:200] if output else f"exit={completed.returncode}"


def check_tool(name: str, required: bool, timeout: float) -> Check:
    lookup = {"git-bash": "bash", "powershell": "pwsh", "python": sys.executable}.get(name, name)
    if name == "python":
        return Check("command:python", "pass", f"{sys.executable} ({sys.version.split()[0]})", required)
    path = shutil.which(lookup)
    if not path:
        return Check(f"command:{name}", "fail" if required else "warn", "not found", required)
    detail = f"{path}; {run_version(lookup, timeout)}"
    if name == "docker":
        try:
            result = subprocess.run([lookup, "info"], capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return Check("docker:daemon", "fail" if required else "warn", str(error), required)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "docker info failed").strip()[:300]
            return Check("docker:daemon", "fail" if required else "warn", detail, required)
        detail += "; daemon ready"
    return Check(f"command:{name}", "pass", detail, required)


def check_port(port: int, required: bool, timeout: float) -> Check:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
    except OSError as error:
        return Check(f"port:{port}", "fail" if required else "warn", str(error), required)
    finally:
        sock.close()
    if result == 0:
        return Check(f"port:{port}", "pass", "listening", required)
    return Check(f"port:{port}", "fail" if required else "warn", "not listening", required)


def check_url(url: str, required: bool, timeout: float) -> Check:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
        return Check(f"url:{url}", "fail" if required else "warn", str(error), required)
    if status >= 500:
        return Check(f"url:{url}", "fail" if required else "warn", f"HTTP {status}", required)
    return Check(f"url:{url}", "pass", f"HTTP {status}", required)


def check_import(module: str, required: bool) -> Check:
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        found = False
    return Check(
        f"python-import:{module}",
        "pass" if found else ("fail" if required else "warn"),
        "available" if found else "not found in current interpreter",
        required,
    )


def check_container(container: str, required: bool, timeout: float) -> Check:
    docker = shutil.which("docker")
    if not docker:
        return Check(f"container:{container}", "fail" if required else "warn", "docker not found", required)
    try:
        result = subprocess.run(
            [docker, "inspect", "--format", "{{.State.Running}}", container],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Check(f"container:{container}", "fail" if required else "warn", str(error), required)
    state = (result.stdout or "").strip().lower()
    if result.returncode == 0 and state == "true":
        return Check(f"container:{container}", "pass", "running", required)
    detail = (result.stderr or result.stdout or "container is not running").strip()[:300]
    return Check(f"container:{container}", "fail" if required else "warn", detail, required)


def check_container_import(container: str, module: str, required: bool, timeout: float) -> Check:
    docker = shutil.which("docker")
    name = f"container-import:{container}:{module}"
    if not docker:
        return Check(name, "fail" if required else "warn", "docker not found", required)
    code = "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
    try:
        result = subprocess.run(
            [docker, "exec", container, "python", "-c", code, module],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Check(name, "fail" if required else "warn", str(error), required)
    if result.returncode == 0:
        return Check(name, "pass", "available in container Python", required)
    detail = (result.stderr or result.stdout or "not found in container Python").strip()[:300]
    return Check(name, "fail" if required else "warn", detail, required)


def check_container_tool(container: str, command: str, required: bool, timeout: float) -> Check:
    docker = shutil.which("docker")
    name = f"container-command:{container}:{command}"
    if not docker:
        return Check(name, "fail" if required else "warn", "docker not found", required)
    try:
        result = subprocess.run(
            [docker, "exec", container, command, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Check(name, "fail" if required else "warn", str(error), required)
    if result.returncode == 0:
        detail = (result.stdout or result.stderr or "available").strip().splitlines()[0][:300]
        return Check(name, "pass", detail, required)
    detail = (result.stderr or result.stdout or "not found in container").strip()[:300]
    return Check(name, "fail" if required else "warn", detail, required)


def run_check(args: argparse.Namespace) -> tuple[list[Check], dict[str, list[str]]]:
    requirements = {
        "shells": [],
        "tools": [],
        "ports": [],
        "urls": [],
        "imports": [],
        "containers": [],
        "container_tools": [],
        "container_imports": [],
    }
    if args.plan:
        requirements = plan_requirements(args.plan)
    requirements["tools"].extend(args.require)
    requirements["ports"].extend(str(port) for port in args.require_port)
    requirements["urls"].extend(args.require_url)
    requirements["imports"].extend(args.require_import)
    requirements["containers"].extend(args.require_container)
    requirements["container_tools"].extend(args.require_container_tool)
    requirements["container_imports"].extend(args.require_container_import)
    checks: list[Check] = []
    required_tools = set(requirements["tools"])
    tools = ["python", "pwsh", "git-bash", "uv", "npm", "docker", "psql"]
    tools.extend(requirements["shells"])
    tools.extend(requirements["tools"])
    for tool in dict.fromkeys(tools):
        checks.append(
            check_tool(
                tool,
                tool in required_tools
                or tool in requirements["shells"]
                or (tool == "docker" and bool(requirements["containers"])),
                args.timeout,
            )
        )
    for port in dict.fromkeys(requirements["ports"]):
        checks.append(check_port(int(port), port in {str(value) for value in args.require_port} or bool(args.plan), args.timeout))
    for url in dict.fromkeys(requirements["urls"]):
        checks.append(check_url(url, url in args.require_url or bool(args.plan), args.timeout))
    for module in dict.fromkeys(requirements["imports"]):
        checks.append(check_import(module, module in args.require_import or bool(args.plan)))
    for container in dict.fromkeys(requirements["containers"]):
        checks.append(check_container(container, True, args.timeout))
    for item in dict.fromkeys(requirements["container_tools"]):
        if ":" not in item:
            checks.append(Check(f"container-command:{item}", "fail", "use CONTAINER:COMMAND", True))
            continue
        container, command = item.split(":", 1)
        checks.append(check_container_tool(container, command, True, args.timeout))
    for item in dict.fromkeys(requirements["container_imports"]):
        if ":" not in item:
            checks.append(Check(f"container-import:{item}", "fail", "use CONTAINER:MODULE", True))
            continue
        container, module = item.split(":", 1)
        checks.append(check_container_import(container, module, True, args.timeout))
    return checks, requirements


def output(checks: list[Check], requirements: dict[str, list[str]], args: argparse.Namespace) -> int:
    failed_required = [item for item in checks if item.required and item.status == "fail"]
    summary = {
        "pass": sum(item.status == "pass" for item in checks),
        "warn": sum(item.status == "warn" for item in checks),
        "fail": sum(item.status == "fail" for item in checks),
        "required_fail": len(failed_required),
    }
    result: dict[str, object] = {"valid": not failed_required, "summary": summary, "requirements": requirements}
    if not args.summary:
        result["checks"] = [item.as_dict() for item in checks]
    elif failed_required:
        result["checks"] = [item.as_dict() for item in checks if item.status == "fail"]
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        print(f"valid={result['valid']} pass={summary['pass']} warn={summary['warn']} fail={summary['fail']}")
        for item in checks:
            if not args.summary or item.status == "fail":
                print(f"{item.status.upper():5} {item.name}: {item.detail}")
    return 0 if not failed_required else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check task execution environment without changing services")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="run shell, tool, port, URL, and Python import checks")
    check.add_argument("--plan", type=Path)
    check.add_argument("--require", action="append", default=[])
    check.add_argument("--require-port", type=int, action="append", default=[])
    check.add_argument("--require-url", action="append", default=[])
    check.add_argument("--require-import", action="append", default=[])
    check.add_argument("--require-container", action="append", default=[])
    check.add_argument("--require-container-tool", action="append", default=[])
    check.add_argument("--require-container-import", action="append", default=[])
    check.add_argument("--timeout", type=float, default=5.0)
    check.add_argument("--format", choices=("json", "table"), default="json")
    check.add_argument("--summary", action="store_true")
    check.set_defaults(function=lambda args: output(*run_check(args), args))
    return parser


if __name__ == "__main__":
    parsed_args = build_parser().parse_args()
    raise SystemExit(parsed_args.function(parsed_args))
