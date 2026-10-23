"""Setup identity/topology checks without contacting a real cluster."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def run_setup(self, scenario):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            command = root / "oc"
            command.write_text('''#!/bin/bash
echo "$*" >> "$AUDIT_CALLS"
case "$1 $2" in
 'whoami ') echo audit-user;;
 'get nodes') echo audit-zone;;
 'get storageclass')
   if [[ "$*" == *jsonpath* ]]; then
     if [[ "$SCENARIO" == immediate ]]; then echo Immediate; else echo WaitForFirstConsumer; fi
   else echo '{"items":[{"metadata":{"name":"test-sc","annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}]}'; fi;;
 'get pvc')
   [[ "$SCENARIO" == missing-pvc ]] && exit 1
   [[ "$*" == *model-id* ]] && echo Qwen/Qwen2.5-3B-Instruct
   [[ "$*" == *model-revision* ]] && echo aa8e72537993ba99e69dfaafa59ed015b17504d1
   [[ "$*" == *metadata.uid* ]] && echo current-pvc
   [[ "$*" == *storageClassName* ]] && echo test-sc;;
 'get job')
   [[ "$*" == *status.succeeded* ]] && echo 1
   [[ "$*" == *model-id* ]] && echo Qwen/Qwen2.5-3B-Instruct
   [[ "$*" == *model-revision* ]] && echo aa8e72537993ba99e69dfaafa59ed015b17504d1
   if [[ "$*" == *pvc-uid* ]]; then
     if [[ "$SCENARIO" == stale-job ]]; then echo old-pvc; else echo current-pvc; fi
   fi;;
 'apply -f') cat >/dev/null;;
 'get project'|'project audit-project'|'label namespace') ;;
 *) echo "Unexpected fixture command: $*" >&2; exit 99;;
esac
exit 0
''')
            command.chmod(0o700)
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "AUDIT_CALLS": str(root / "calls"), "SCENARIO": scenario, "HF_TOKEN": ""}
            run = subprocess.run(["bash", str(ROOT / "01-setup-project.sh"), "audit-project"],
                                 env=env, capture_output=True, text=True, timeout=10)
            self.assertNotIn("Unexpected fixture command", run.stderr)
            return run, (root / "calls").read_text()

    def test_missing_pvc_does_not_reuse_completed_download(self):
        run, calls = self.run_setup("missing-pvc")
        self.assertNotEqual(run.returncode, 0)
        self.assertNotIn("apply -f", calls)

    def test_stale_job_is_not_certification_of_current_pvc(self):
        run, _ = self.run_setup("stale-job")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Job identity", run.stderr)

    def test_immediate_storage_is_rejected_before_claim_creation(self):
        run, calls = self.run_setup("immediate")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("WaitForFirstConsumer", run.stderr)
        self.assertNotIn("apply -f", calls)

    def test_matching_completed_download_is_reused(self):
        run, calls = self.run_setup("matching")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("already downloaded", run.stdout)
        self.assertNotIn("delete job", calls)


if __name__ == "__main__":
    unittest.main()
