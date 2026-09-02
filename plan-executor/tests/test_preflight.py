from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "preflight.py"


class PreflightCliTests(unittest.TestCase):
    def run_cli(self, *arguments: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            self.fail(f"command failed: {completed.args}\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_required_command_failure_is_structured_and_summary_is_compact(self) -> None:
        result = self.run_cli(
            "check",
            "--require",
            "command-that-does-not-exist-for-plan-executor",
            "--format",
            "json",
            "--summary",
            check=False,
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["summary"]["required_fail"], 1)
        self.assertEqual(len(result["checks"]), 1)

    def test_plan_requirements_include_container_and_runtime_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "T01-plan.md"
            plan.write_text(
                """## 环境预检
- 必需 Shell：pwsh, git-bash
- 必需命令：python, docker
- 必需端口：8000, 5432
- 必需 URL：http://127.0.0.1:8000/health
- 必需 Python 模块：psycopg2
- 必需 Docker 容器：backend, pg
- 容器内必需 Python 模块：backend:psycopg2
- 预检命令：python preflight.py check --format json
""",
                encoding="utf-8",
            )
            result = self.run_cli("check", "--plan", str(plan), "--format", "json", "--summary", check=False)
            self.assertEqual(result["requirements"]["containers"], ["backend", "pg"])
            self.assertEqual(result["requirements"]["container_imports"], ["backend:psycopg2"])
            self.assertEqual(result["requirements"]["ports"], ["8000", "5432"])


if __name__ == "__main__":
    unittest.main()
