"""框架识别与入口路由适配器注册表。

新增框架时优先在 FRAMEWORKS 增加元数据，再在 detect_routes() 增加最小、可证明的入口模式。
无法识别的入口必须返回 [UNKNOWN: handler]，不能根据目录名猜测。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

FRAMEWORKS: dict[str, dict[str, Any]] = {
    "generic": {"label": "通用项目", "template": "generic", "evidence": (), "languages": ()},
    "fastapi": {"label": "Python FastAPI", "template": "python-api", "evidence": ("fastapi", "starlette"), "languages": ("python",)},
    "flask": {"label": "Python Flask", "template": "python-api", "evidence": ("flask",), "languages": ("python",)},
    "django": {"label": "Python Django", "template": "django-layered", "evidence": ("django",), "languages": ("python",)},
    "express": {"label": "Node Express", "template": "node-api", "evidence": ("express",), "languages": ("javascript", "typescript")},
    "nestjs": {"label": "Node NestJS", "template": "node-modular", "evidence": ("@nestjs/", "nestjs"), "languages": ("typescript",)},
    "next": {"label": "Next.js", "template": "next-fullstack", "evidence": ("next",), "languages": ("javascript", "typescript")},
}


def _dependency_text(root: Path) -> tuple[str, list[str]]:
    pieces: list[str] = []
    files: list[str] = []
    names = ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "Pipfile", "package.json", "pnpm-lock.yaml", "yarn.lock")
    for name in names:
        path = root / name
        if path.is_file():
            # 只用于匹配公开依赖名称，不把内容写入扫描结果。
            try:
                pieces.append(path.read_text(encoding="utf-8", errors="ignore").lower())
            except OSError:
                pass
            files.append(name)
    return "\n".join(pieces), files


def detect_framework(root: Path, requested: str = "auto") -> dict[str, Any]:
    if requested != "auto":
        item = FRAMEWORKS.get(requested, FRAMEWORKS["generic"])
        return {"id": requested if requested in FRAMEWORKS else "generic", "label": item["label"], "template": item["template"], "mode": "explicit", "evidence": ["--framework " + requested]}
    text, files = _dependency_text(root)
    candidates: list[tuple[int, str, list[str]]] = []
    for framework, item in FRAMEWORKS.items():
        if framework == "generic":
            continue
        hits = [needle for needle in item["evidence"] if needle in text]
        if hits:
            candidates.append((len(hits), framework, hits))
    # 文件布局是辅助证据；不单独凭目录名称判断框架。
    if (root / "manage.py").is_file() and not any(name == "django" for _, name, _ in candidates):
        candidates.append((1, "django", ["manage.py"]))
    if (root / "next.config.js").is_file() or (root / "next.config.mjs").is_file():
        candidates.append((1, "next", ["next.config.*"]))
    if candidates:
        _, framework, hits = sorted(candidates, reverse=True)[0]
        item = FRAMEWORKS[framework]
        return {"id": framework, "label": item["label"], "template": item["template"], "mode": "auto", "evidence": hits + files[:3]}
    return {"id": "generic", "label": FRAMEWORKS["generic"]["label"], "template": "generic", "mode": "auto", "evidence": files[:3]}


def detect_routes(path: Path, text: str, framework: str) -> list[dict[str, str]]:
    """只返回语法模式直接证明的 route；handler 无法追踪时明确标 UNKNOWN。"""
    result: list[dict[str, str]] = []
    suffix = path.suffix.lower()
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        for match in re.finditer(r"(?:app|router)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)", text, re.I):
            result.append({"method": match.group(1).upper(), "path": match.group(2), "line": str(text[:match.start()].count("\n") + 1), "function": "[UNKNOWN: handler]", "framework": framework})
        if framework == "nestjs":
            for match in re.finditer(r"@(Get|Post|Put|Patch|Delete|Options|Head)\(\s*['\"]?([^'\")\s]*)", text):
                result.append({"method": match.group(1).upper(), "path": match.group(2) or "/", "line": str(text[:match.start()].count("\n") + 1), "function": "[UNKNOWN: handler]", "framework": framework})
        if framework == "next" and ("/pages/api/" in path.as_posix() or "/app/api/" in path.as_posix()):
            result.append({"method": "FILE_ROUTE", "path": "/" + path.as_posix().split("/api/", 1)[1].rsplit("/route.", 1)[0].rsplit(".", 1)[0], "line": "1", "function": "[UNKNOWN: file handler]", "framework": framework})
    if suffix == ".py" and framework == "django" and path.name in {"urls.py", "routes.py"}:
        for match in re.finditer(r"\b(?:path|re_path)\(\s*['\"]([^'\"]+)", text):
            result.append({"method": "URLPATTERN", "path": match.group(1), "line": str(text[:match.start()].count("\n") + 1), "function": "[UNKNOWN: view]", "framework": framework})
    return result
