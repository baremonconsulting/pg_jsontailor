"""Installation cannot consume the reserved product-test budget."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.install_postgresql import install, validate_budget


class InstallationBudget(unittest.TestCase):
    def test_budget_rejects_insufficient_test_reserve(self):
        for budget, job, reserve in [(481, 45, 25), (480, 25, 25), (480, 45, 24), (0, 45, 25)]:
            with self.subTest(budget=budget, job=job, reserve=reserve), self.assertRaises(RuntimeError):
                validate_budget(budget, job, reserve)

    def test_every_command_has_a_hard_timeout_and_failure_retains_diagnostics(self):
        calls = []
        def fake(command, **kwargs):
            calls.append((command, kwargs))
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 180)
            kwargs["stdout"].write("diagnostic sentinel\n")
            if len(calls) == 2:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "installation.log"
            with self.assertRaisesRegex(RuntimeError, "bounded PostgreSQL installation failed"):
                install("17", log, run=fake)
            self.assertIn("diagnostic sentinel", log.read_text())
            self.assertIn("installation failed", log.read_text())
            self.assertEqual(len(calls), 2)

    def test_workflow_installer_and_build_have_step_limits(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
        self.assertIn("timeout-minutes: 45", workflow)
        self.assertIn("packages\n        timeout-minutes: 9", workflow)
        self.assertIn("candidate\n        timeout-minutes: 15", workflow)
        self.assertIn("tools/install_postgresql.py", workflow)


if __name__ == "__main__":
    unittest.main()
