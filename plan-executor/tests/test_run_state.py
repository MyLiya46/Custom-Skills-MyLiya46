from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_state.py"


class RunStateCliTests(unittest.TestCase):
    def run_cli(self, *arguments: str, check: bool = True) -> dict | list:
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

    def init_run(self, root: Path, task: str = "T03") -> dict:
        return self.run_cli(
            "init",
            "--state-dir",
            str(root),
            "--task",
            task,
            "--mode",
            "worker",
            "--profile",
            "long-infra",
            "--external-wait",
            "PostgreSQL",
            "--checkpoint-phase",
            "migration",
        )

    def test_checkpoint_inspect_and_finish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root)
            state_file = created["state_file"]

            acknowledged = self.run_cli(
                "checkpoint",
                "--state-file",
                state_file,
                "--status",
                "acknowledged",
                "--phase",
                "dispatch",
            )
            self.assertEqual(acknowledged["status"], "acknowledged")

            started = self.run_cli(
                "phase-start",
                "--state-file",
                state_file,
                "--phase",
                "migration",
                "--current-command",
                "uv run alembic upgrade head",
            )
            self.assertEqual(started["phase_history"][-1]["event"], "phase_started")

            completed = self.run_cli(
                "phase-complete",
                "--state-file",
                state_file,
                "--phase",
                "migration",
                "--message",
                "migration checkpoint passed",
                "--next-checkpoint",
                "acceptance",
            )
            self.assertEqual(completed["resume_from"], "acceptance")
            self.assertEqual(completed["phase_history"][-1]["event"], "phase_completed")

            waiting = self.run_cli(
                "checkpoint",
                "--state-file",
                state_file,
                "--status",
                "waiting_external",
                "--phase",
                "migration",
                "--current-command",
                "uv run alembic upgrade head",
                "--changed-file",
                "backend/alembic/versions/0003.py",
            )
            self.assertEqual(waiting["status"], "waiting_external")
            self.assertEqual(waiting["changed_files"], ["backend/alembic/versions/0003.py"])

            observed = self.run_cli(
                "inspect",
                "--state-file",
                state_file,
                "--idle-timeout",
                "3600",
            )
            self.assertFalse(observed["observation"]["terminal"])
            self.assertFalse(observed["observation"]["idle_exceeded"])
            self.assertTrue(observed["observation"]["active_command_present"])

            finished = self.run_cli(
                "finish",
                "--state-file",
                state_file,
                "--status",
                "completed",
                "--acceptance-json",
                '{"command":"pytest","exit_code":0}',
            )
            self.assertEqual(finished["status"], "completed")
            self.assertEqual(finished["acceptance"]["exit_code"], 0)

    def test_active_duplicate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.init_run(root, "T04")
            duplicate = self.run_cli(
                "init",
                "--state-dir",
                str(root),
                "--task",
                "T04",
                check=False,
            )
            self.assertEqual(duplicate, {})

    def test_reconcile_resume_preserves_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T06")
            state_file = created["state_file"]
            self.run_cli(
                "checkpoint",
                "--state-file",
                state_file,
                "--phase",
                "submit-model-task",
                "--changed-file",
                "backend/src/app/services/forecast_client.py",
            )
            reconciled = self.run_cli(
                "reconcile",
                "--state-file",
                state_file,
                "--reason",
                "agent result was delayed; process checked before resume",
            )
            self.assertEqual(reconciled["status"], "reconciling")
            resumed = self.run_cli(
                "resume",
                "--state-file",
                state_file,
                "--phase",
                "poll-model-task",
            )
            self.assertEqual(resumed["status"], "running")
            self.assertEqual(resumed["attempt"], 2)
            self.assertEqual(resumed["mode"], "worker")
            self.assertEqual(
                resumed["changed_files"],
                ["backend/src/app/services/forecast_client.py"],
            )
            self.assertEqual(resumed["phase_history"][-2]["event"], "reconciled")
            self.assertEqual(resumed["phase_history"][-1]["event"], "resumed")

    def test_direct_mode_is_preserved_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.run_cli(
                "init",
                "--state-dir",
                str(root),
                "--task",
                "T07",
                "--mode",
                "direct",
            )
            resumed = self.run_cli(
                "resume",
                "--state-file",
                created["state_file"],
                "--phase",
                "auth",
            )
            self.assertEqual(resumed["mode"], "direct")

    def test_active_pid_is_observable_during_delayed_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T15")
            state_file = created["state_file"]
            sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
            try:
                self.run_cli(
                    "checkpoint",
                    "--state-file",
                    state_file,
                    "--status",
                    "waiting_external",
                    "--phase",
                    "e2e-model",
                    "--current-command",
                    "model request still running",
                    "--pid",
                    str(sleeper.pid),
                )
                time.sleep(0.05)
                observed = self.run_cli("inspect", "--state-file", state_file)
                self.assertTrue(observed["observation"]["pid_alive"])
                self.assertFalse(observed["observation"]["terminal"])
            finally:
                sleeper.wait(timeout=2)

    def test_validate_reports_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "T08" / "broken.json"
            corrupt.parent.mkdir()
            corrupt.write_text("{not-json", encoding="utf-8")
            result = self.run_cli("validate", "--state-dir", str(root), check=False)
            self.assertFalse(result["valid"])
            self.assertTrue(any("cannot read run state" in error for error in result["errors"]))

    def test_validate_reports_duplicate_active_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T09")
            source = Path(created["state_file"])
            duplicate = source.with_name("duplicate.json")
            duplicate.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            result = self.run_cli("validate", "--state-dir", str(root), check=False)
            self.assertFalse(result["valid"])
            self.assertTrue(any("duplicate active runs" in error for error in result["errors"]))

    def test_validate_and_repair_terminal_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T10")
            state_file = Path(created["state_file"])
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state.update({"status": "completed", "acceptance": {"command": "pytest", "exit_code": 0}})
            state_file.write_text(json.dumps(state), encoding="utf-8")

            invalid = self.run_cli("validate", "--state-file", str(state_file), check=False)
            self.assertFalse(invalid["valid"])
            self.assertTrue(any("finished_at" in error for error in invalid["errors"]))

            repaired = self.run_cli("repair", "--state-file", str(state_file))
            self.assertTrue(repaired["valid"])
            valid = self.run_cli("validate", "--state-file", str(state_file))
            self.assertTrue(valid["valid"])

    def test_invalid_resume_boundary_is_not_silently_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T11")
            state_file = Path(created["state_file"])
            state = json.loads(state_file.read_text(encoding="utf-8"))
            state["resume_from"] = "terminal"
            state_file.write_text(json.dumps(state), encoding="utf-8")
            result = self.run_cli("validate", "--state-file", str(state_file), check=False)
            self.assertFalse(result["valid"])
            self.assertTrue(any("resume_from" in error for error in result["errors"]))

    def test_resume_from_blocked_clears_terminal_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T13")
            state_file = created["state_file"]
            blocked = self.run_cli(
                "finish",
                "--state-file",
                state_file,
                "--status",
                "blocked",
                "--reason",
                "dependency unavailable",
                "--resume-from",
                "migration",
            )
            self.assertEqual(blocked["status"], "blocked")
            resumed = self.run_cli(
                "resume",
                "--state-file",
                state_file,
                "--phase",
                "migration",
                "--allow-terminal-resume",
            )
            self.assertEqual(resumed["status"], "running")
            self.assertNotIn("finished_at", resumed)
            self.assertIsNone(resumed["blocked_reason"])
            valid = self.run_cli("validate", "--state-file", state_file)
            self.assertTrue(valid["valid"])

    def test_repair_leaves_corrupt_json_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            corrupt = root / "T12" / "broken.json"
            corrupt.parent.mkdir()
            original = "{broken"
            corrupt.write_text(original, encoding="utf-8")
            result = self.run_cli("repair", "--state-file", str(corrupt), check=False)
            self.assertFalse(result["valid"])
            self.assertEqual(corrupt.read_text(encoding="utf-8"), original)

    def test_finish_preserves_acceptance_and_terminal_run_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T16")
            state_file = created["state_file"]
            self.run_cli(
                "checkpoint",
                "--state-file",
                state_file,
                "--phase",
                "offline-acceptance",
                "--status",
                "validating",
            )
            finished = self.run_cli(
                "finish",
                "--state-file",
                state_file,
                "--status",
                "blocked",
                "--reason",
                "external backend unavailable",
                "--resume-from",
                "external-acceptance",
                "--acceptance-json",
                '{"type":"offline","command":"pytest","exit_code":0}',
            )
            self.assertEqual(finished["acceptance"]["exit_code"], 0)
            preserved = self.run_cli(
                "finish",
                "--state-file",
                state_file,
                "--status",
                "blocked",
                "--reason",
                "should be rejected",
                "--resume-from",
                "resume",
                check=False,
            )
            self.assertEqual(preserved, {})
            checkpoint = self.run_cli(
                "checkpoint",
                "--state-file",
                state_file,
                "--status",
                "running",
                "--phase",
                "reminder",
                check=False,
            )
            self.assertEqual(checkpoint, {})
            unapproved_resume = self.run_cli(
                "resume",
                "--state-file",
                state_file,
                "--phase",
                "external-acceptance",
                check=False,
            )
            self.assertEqual(unapproved_resume, {})
            resumed = self.run_cli(
                "resume",
                "--state-file",
                state_file,
                "--phase",
                "external-acceptance",
                "--allow-terminal-resume",
            )
            self.assertEqual(resumed["status"], "running")
            self.assertEqual(resumed["acceptance"]["exit_code"], 0)

    def test_completed_offline_and_summary_are_first_class(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.run_cli(
                "init",
                "--state-dir",
                str(root),
                "--task",
                "T17",
                "--checkpoint-interval",
                "30",
            )
            state_file = created["state_file"]
            finished = self.run_cli(
                "finish",
                "--state-file",
                state_file,
                "--status",
                "completed_offline",
                "--acceptance-note",
                "offline tests passed",
            )
            self.assertEqual(finished["status"], "completed_offline")
            summary = self.run_cli("summary", "--state-file", state_file)
            self.assertEqual(summary["status"], "completed_offline")
            self.assertTrue(summary["acceptance_recorded"])
            listing = self.run_cli("list", "--state-dir", str(root), "--summary")
            self.assertEqual(listing[0]["checkpoint_count"], 1)

    def test_validate_reports_plan_run_drift_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = self.init_run(root, "T18")
            plan_state = root / "todo.json"
            plan_state.write_text(
                json.dumps({"tasks": [{"id": "T18", "status": "completed"}]}),
                encoding="utf-8",
            )
            result = self.run_cli(
                "validate",
                "--state-dir",
                str(root),
                "--plan-state",
                str(plan_state),
                "--summary",
                check=False,
            )
            self.assertTrue(result["valid"])
            self.assertTrue(result["warnings"])
            self.assertIn("completed", result["warnings"][0])


if __name__ == "__main__":
    unittest.main()
