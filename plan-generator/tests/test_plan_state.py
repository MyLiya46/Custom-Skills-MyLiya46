from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "plan_state.py"


VALID_PLAN = """# T01 · 示例计划

- 任务 ID：T01
- 前置依赖 blockedBy：无

## 问题
现有校验只读取任务表，无法发现正文缺失。

## 决策
使用固定章节和结构化字段作为评审门槛。

## 范围
- 包含：计划模板和静态校验。
- 不包含：业务源码。

## 风险与回滚
- 风险：旧计划不符合新结构，评审会被阻断。
- 回滚：使用 --skip-plan-check 读取旧状态，并逐份补齐正文。

## 实施步骤
### 步骤 1：增加 lint
- 对象：plan_state.py
- 动作：增加正文校验
- 参数：固定章节、命令和字段
- 文件：plan-generator/scripts/plan_state.py
- 命令：
  ```bash
  python plan-generator/scripts/plan_state.py lint-plan --plan docs/plans/T01-demo.md
  ```

## 完成标准
- 验收命令：
  ```bash
  python -m unittest discover -s plan-generator/tests -v
  ```
- 通过条件：退出码为 0，所有断言通过。
"""


class PlanStateLintTests(unittest.TestCase):
    def run_cli(self, *arguments: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            self.fail(f"command failed: {completed.args}\n{completed.stderr}")
        if not completed.stdout.strip():
            return {}
        return json.loads(completed.stdout)

    def write_fixture(self, root: Path, plan_text: str = VALID_PLAN) -> Path:
        docs = root / "docs"
        plans = docs / "plans"
        plans.mkdir(parents=True)
        (docs / "todo.md").write_text(
            "| 序号 | 任务ID | 标题 | blockedBy | 状态 | 负责人 | 计划 |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | T01 | 示例 | 无 | pending | - | [plan](plans/T01-demo.md) |\n",
            encoding="utf-8",
        )
        plan = plans / "T01-demo.md"
        plan.write_text(plan_text, encoding="utf-8")
        return plan

    def test_valid_plan_passes_lint_and_todo_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.write_fixture(root)
            linted = self.run_cli("lint-plan", "--plan", str(plan), "--task", "T01")
            self.assertTrue(linted["valid"])
            validated = self.run_cli("validate", "--todo", str(root / "docs/todo.md"))
            self.assertTrue(validated["valid"])

    def test_missing_ordered_section_and_todo_token_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.write_fixture(root, VALID_PLAN.replace("## 决策", "## TODO决策").replace("现有校验", "TODO\n现有校验"))
            result = self.run_cli("lint-plan", "--plan", str(plan), check=False)
            self.assertFalse(result["valid"])
            self.assertTrue(any("forbidden TBD/TODO" in error for error in result["errors"]))
            self.assertTrue(any("missing required sections" in error for error in result["errors"]))

    def test_blocked_by_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.write_fixture(root, VALID_PLAN.replace("blockedBy：无", "blockedBy：T02"))
            result = self.run_cli(
                "lint-plan", "--plan", str(plan), "--task", "T01", "--blocked-by", check=False
            )
            self.assertFalse(result["valid"])
            self.assertTrue(any("blockedBy does not match" in error for error in result["errors"]))

    def test_json_is_canonical_and_markdown_is_exported_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self.write_fixture(root)
            docs = root / "docs"
            state_file = docs / "todo.json"
            self.run_cli("import-md", "--todo", str(docs / "todo.md"), "--state", str(state_file))

            validated = self.run_cli("validate", "--state", str(state_file))
            self.assertTrue(validated["valid"])
            self.assertEqual(validated["revision"], 0)

            reviewed = self.run_cli("review", "--state", str(state_file), "--task", "T01", "--if-revision", "0")
            self.assertEqual(reviewed["revision"], 1)
            claimed = self.run_cli("claim", "--state", str(state_file), "--task", "T01", "--if-revision", "1")
            self.assertEqual(claimed["revision"], 2)
            completed = self.run_cli(
                "complete",
                "--state",
                str(state_file),
                "--task",
                "T01",
                "--if-revision",
                "2",
                "--acceptance-note",
                "all assertions passed",
            )
            self.assertEqual(completed["revision"], 3)

            stale = self.run_cli(
                "review",
                "--state",
                str(state_file),
                "--task",
                "T01",
                "--if-revision",
                "0",
                check=False,
            )
            self.assertEqual(stale, {})

            rendered = docs / "todo-rendered.md"
            self.run_cli("export-md", "--state", str(state_file), "--todo", str(rendered))
            text = rendered.read_text(encoding="utf-8")
            self.assertIn("GENERATED FROM docs/todo.json", text)
            self.assertIn("| completed |", text)
            synced = self.run_cli("export-md", "--state", str(state_file), "--todo", str(rendered), "--check", check=False)
            expected = docs / "expected.md"
            self.run_cli("export-md", "--state", str(state_file), "--todo", str(expected))
            self.assertEqual(rendered.read_text(encoding="utf-8"), expected.read_text(encoding="utf-8"))
            self.assertTrue(synced["in_sync"], synced)
            rendered.write_text(text + "manual edit\n", encoding="utf-8")
            drifted = self.run_cli("export-md", "--state", str(state_file), "--todo", str(rendered), "--check", check=False)
            self.assertFalse(drifted["in_sync"])


if __name__ == "__main__":
    unittest.main()
