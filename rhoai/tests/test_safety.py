"""Control-flow regressions: real shell entry points, isolated command fixtures."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SafetyTests(unittest.TestCase):
    def run_orchestrator(self, base_url=""):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copy2(ROOT / "00-run-lab.sh", root / "00-run-lab.sh")
            for step in ("01-setup-project.sh", "04-run-inference.sh", "05-cleanup.sh"):
                (root / step).write_text('#!/bin/bash\necho "$0 $*" >> "$AUDIT_CALLS"\n')
            oc = root / "oc"
            oc.write_text('''#!/bin/bash
echo "oc $*" >> "$AUDIT_CALLS"
case "$1" in
 new-project) echo 'AlreadyExists (injected race)' >&2; exit 1;;
 get)
   case "$2" in
     project) exit 1;;
     template) echo registry.example/runtime@sha256:test;;
   esac;;
 create)
   if [[ "$*" != *dry-run* ]]; then cat >/dev/null; echo 'AlreadyExists (injected race)' >&2; exit 1; fi
   echo '{"spec":{"containers":[{"image":"original"}]}}';;
 apply) if [[ "$*" == *'-f -'* ]]; then cat >/dev/null; fi;;
esac
exit 0
''')
            oc.chmod(0o700)
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "AUDIT_CALLS": str(root / "calls"), "BASE_URL": base_url}
            run = subprocess.run(["bash", str(root / "00-run-lab.sh"), "verify", "audit-race"],
                                 env=env, capture_output=True, text=True, timeout=10)
            calls = (root / "calls").read_text() if (root / "calls").exists() else ""
            return run, calls

    def test_verify_requires_successful_exclusive_project_creation(self):
        run, calls = self.run_orchestrator()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("AlreadyExists", run.stderr)
        self.assertNotIn("05-cleanup.sh", calls)
        self.assertNotIn("01-setup-project.sh", calls)

    def test_verify_rejects_inherited_external_endpoint_before_any_mutation(self):
        run, calls = self.run_orchestrator("http://127.0.0.1:9")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("BASE_URL", run.stderr)
        self.assertEqual(calls, "")


if __name__ == "__main__":
    unittest.main()
