"""Shell entry-point regressions with isolated external-command fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from failure_probes import intended_failure


class ShellTests(unittest.TestCase):
    def test_failures_are_propagated_by_all_entry_points(self):
        root = Path(__file__).resolve().parents[1]
        run = subprocess.run([sys.executable, str(root / "tests" / "failure_probes.py"), str(root)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        evidence = json.loads(run.stdout)
        self.assertEqual(len(evidence["results"]), 6)
        for item in evidence["results"]:
            with self.subTest(item["test"]):
                self.assertNotEqual(item["exit_code"], 0)
                self.assertTrue(item["intended_failure_verified"])

    def test_broken_fixtures_cannot_pass_as_injected_failures(self):
        for code, message in ((99, "Unexpected fixture command"), (1, "Traceback: broken harness"), (2, "missing dependency")):
            self.assertFalse(intended_failure("mock_forbidden_cleanup", code, "", message))
        self.assertFalse(intended_failure("real_http_500_benchmark", 1, "{}", "HTTP 500"))


if __name__ == "__main__":
    unittest.main()
