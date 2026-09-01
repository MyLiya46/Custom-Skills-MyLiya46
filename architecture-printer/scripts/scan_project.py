#!/usr/bin/env python3
"""标准库项目结构扫描器：输出可审计的模块、声明、路由、导入和配置元数据。"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from frameworks import detect_framework, detect_routes

EXCLUDED = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", "dist", "build", "coverage", "architecture-printer", "docs/architecture"}
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".rb", ".php", ".rs", ".cs"}
CONFIG_NAMES = {".env", ".env.example", ".env.sample", "docker-compose.yml", "docker-compose.yaml", "pyproject.toml", "package.json", "vite.config.ts", "vite.config.js"}
SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|cookie)\s*([:=])\s*(['\"]?)[^\s,'\"}]+")


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def excluded(path: Path, root: Path) -> bool:
    value = rel(path, root)
    parts = set(Path(value).parts)
    return any(item in parts for item in EXCLUDED) or value == "docs/architecture"


def scope_match(path: Path, root: Path, scope: str) -> bool:
    if scope == "full":
        return True
    value = rel(path, root).lower()
    if scope == "backend":
        return any(x in value.split("/") for x in ("backend", "server", "app")) or path.suffix == ".py"
    if scope == "frontend":
        return path.suffix in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html", ".css"} and any(x in value.split("/") for x in ("frontend", "client", "web", "ui", "src"))
    if scope == "integration":
        needles = ("api", "route", "client", "adapter", "gateway", "integration", "service", "config", "docker", "compose")
        return any(n in value for n in needles)
    return True


def iter_files(root: Path, scope: str) -> tuple[list[Path], int, bool]:
    files: list[Path] = []
    all_count = 0
    for base, dirs, names in os.walk(root):
        base_path = Path(base)
        dirs[:] = [d for d in dirs if d not in EXCLUDED and not excluded(base_path / d, root)]
        for name in names:
            path = base_path / name
            all_count += 1
            if (path.suffix.lower() in CODE_EXTS or name in CONFIG_NAMES or name.startswith(".env")) and scope_match(path, root, scope):
                files.append(path)
    reduced = all_count > 5000
    if reduced:
        allowed = {"src", "app", "backend", "frontend", "tests", "test"}
        files = [p for p in files if any(part.lower() in allowed for part in Path(rel(p, root)).parts) or p.name.startswith("main.") or p.name in CONFIG_NAMES]
    return sorted(files), all_count, reduced


def source_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def redact_text(value: str) -> str:
    """保留签名/说明的形状，不把可能出现在默认参数或 docstring 中的凭据带出。"""
    return SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]", value)


def signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        try:
            args = ast.unparse(node.args)
        except Exception:
            args = "..."
        return redact_text(f"{prefix}def {node.name}({args})")
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(x) for x in node.bases) if node.bases else ""
        return redact_text(f"class {node.name}({bases})" if bases else f"class {node.name}")
    return ""


def decorator_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def route_from_decorators(node: ast.AST) -> list[dict[str, str]]:
    result = []
    for decorator in getattr(node, "decorator_list", []):
        text = decorator_text(decorator)
        match = re.search(r"(?:app|router|api|bp)\.([a-zA-Z]+)\(\s*['\"]([^'\"]+)", text)
        if match and match.group(1).lower() in {"get", "post", "put", "patch", "delete", "options", "head", "route", "websocket"}:
            result.append({"method": match.group(1).upper(), "path": match.group(2), "decorator": text[:180]})
    return result


def python_scan(path: Path, root: Path, framework: str) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    text = source_text(path)
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [], [], [{"file": rel(path, root), "kind": "parse_error", "detail": f"line {exc.lineno}: {exc.msg}"}]
    declarations: list[dict[str, Any]] = []
    routes: list[dict[str, str]] = []
    imports: list[dict[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node) or ""
            item = {"file": rel(path, root), "line": getattr(node, "lineno", 1), "name": node.name, "kind": "class" if isinstance(node, ast.ClassDef) else "function", "visibility": "private" if node.name.startswith("_") else "public", "signature": signature(node), "docstring": redact_text(doc.strip().splitlines()[0][:240]) if doc else "", "async": isinstance(node, ast.AsyncFunctionDef)}
            found_routes = route_from_decorators(node) if not isinstance(node, ast.ClassDef) else []
            if found_routes:
                item["routes"] = found_routes
                for route in found_routes:
                    routes.append({**route, "file": item["file"], "line": str(item["line"]), "function": node.name})
            declarations.append(item)
        elif isinstance(node, ast.Import):
            imports.extend({"file": rel(path, root), "raw": alias.name, "target": f"[UNKNOWN: {alias.name}]", "kind": "import"} for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            raw = ("." * node.level) + (node.module or "")
            imports.append({"file": rel(path, root), "raw": raw, "target": f"[UNKNOWN: {raw}]", "kind": "import"})
    return declarations, imports, []


def resolve_import(raw: str, source: Path, root: Path) -> str | None:
    if not raw:
        return None
    candidates = []
    if raw.startswith("."):
        level = len(raw) - len(raw.lstrip("."))
        module_name = raw[level:]
        base = source.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        module = base.joinpath(*module_name.split(".")) if module_name else base
        candidates.extend((module.with_suffix(".py"), module / "__init__.py"))
    else:
        bases = (root, root / "src", root / "backend", root / "frontend")
        for base in bases:
            module = base.joinpath(*raw.split("."))
            candidates.extend((module.with_suffix(".py"), module / "__init__.py"))
    for candidate in candidates:
        if candidate.is_file() and not excluded(candidate, root):
            return rel(candidate, root)
    return None


def config_scan(path: Path, root: Path) -> tuple[list[str], list[str]]:
    if path.name == ".env" or path.name.startswith(".env."):
        text = source_text(path)
        keys = re.findall(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=", text, re.MULTILINE)
        return sorted(set(keys)), []
    text = source_text(path)
    keys = set(re.findall(r"^\s*([A-Za-z][A-Za-z0-9_.-]{2,80})\s*(?:=|:)", text, re.MULTILINE))
    hosts = set()
    for match in re.finditer(r"https?://([^/\s'\"?#]+)([^\s'\"#]*)", text):
        host = match.group(1).split("@")[ -1]
        if len(host) < 160:
            hosts.add(host)
    return sorted(keys), sorted(hosts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan project structure without third-party dependencies")
    parser.add_argument("target_dir", type=Path)
    parser.add_argument("--scope", choices=("full", "backend", "frontend", "integration"), default="full")
    parser.add_argument("--framework", choices=("auto", "generic", "fastapi", "flask", "django", "express", "nestjs", "next"), default="auto", help="框架模板；auto 根据依赖和入口文件选择")
    parser.add_argument("-o", "--output", type=Path, help="Write JSON to file; stdout when omitted")
    args = parser.parse_args()
    root = args.target_dir.resolve()
    if not root.is_dir():
        print(f"目标目录不存在: {root}", file=sys.stderr)
        return 2
    framework_info = detect_framework(root, args.framework)
    files, all_count, reduced = iter_files(root, args.scope)
    declarations: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    routes: list[dict[str, str]] = []
    configs: dict[str, dict[str, Any]] = {}
    for path in files:
        if path.suffix.lower() == ".py":
            text = source_text(path)
            found, found_edges, found_errors = python_scan(path, root, framework_info["id"])
            declarations.extend(found)
            routes.extend([r for item in found for r in item.get("routes", []) for r in [{**r, "file": item["file"], "line": str(item["line"]), "function": item["name"]}]])
            edges.extend(found_edges)
            errors.extend(found_errors)
            for edge in found_edges:
                resolved = resolve_import(edge["raw"], path, root)
                if resolved:
                    edge["target"] = resolved
            routes.extend({**route, "file": rel(path, root)} for route in detect_routes(path, text, framework_info["id"]))
        elif path.suffix.lower() in CODE_EXTS:
            text = source_text(path)
            for match in re.finditer(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", text):
                name = match.group(1) or match.group(2)
                declarations.append({"file": rel(path, root), "line": text[:match.start()].count("\n") + 1, "name": name, "kind": "function", "visibility": "private" if name.startswith("_") else "public", "signature": f"function {name}(…)", "docstring": "", "async": "async" in match.group(0)})
            routes.extend({**route, "file": rel(path, root)} for route in detect_routes(path, text, framework_info["id"]))
        if path.name in CONFIG_NAMES or path.name.startswith(".env") or path.suffix.lower() in {".toml", ".yaml", ".yml", ".ini", ".json"}:
            keys, hosts = config_scan(path, root)
            if keys or hosts:
                configs[rel(path, root)] = {"keys": keys, "hosts": hosts}
    # 去重，同时保留事实来源字段；不合并可能是不同声明的同名函数。
    unique_edges = {(e["file"], e["raw"], e["target"]): e for e in edges}
    unique_routes = {(r["method"], r["path"], r["file"], r["line"]): r for r in routes}
    modules = sorted({item["file"] for item in declarations} | {e["file"] for e in edges} | set(configs))
    result = {"schema_version": 1, "target_dir": str(root), "scope": args.scope, "framework": framework_info, "generated_at": datetime.now(timezone.utc).isoformat(), "stats": {"filesystem_files": all_count, "scanned_files": len(files), "lines": sum(source_text(p).count("\n") + 1 for p in files), "declarations": len(declarations), "routes": len(unique_routes), "reduced_for_size": reduced}, "modules": modules, "declarations": declarations, "routes": list(unique_routes.values()), "edges": list(unique_edges.values()), "configs": configs, "errors": errors, "unknown_policy": "Unresolved imports are emitted as [UNKNOWN: module] and must not be inferred."}
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
