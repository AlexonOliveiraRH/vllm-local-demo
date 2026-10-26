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
 'get nodes') echo '{"items":[{"metadata":{"name":"gpu-a","labels":{"kubernetes.io/hostname":"gpu-a","nvidia.com/gpu.present":"true","nvidia.com/gpu.compute.major":"8","nvidia.com/gpu.compute.minor":"9"}},"spec":{},"status":{"allocatable":{"nvidia.com/gpu":"1"},"conditions":[{"type":"Ready","status":"True"}]}}]}';;
 'get pods') echo '{"items":[]}';;
 'create --dry-run=client') echo '{"spec":{"predictor":{"model":{"resources":{"requests":{"nvidia.com/gpu":"1"},"limits":{"nvidia.com/gpu":"1"}}}}}}';;
 'get storageclass')
   if [[ "$3" == test-sc ]]; then
     if [[ "$SCENARIO" == immediate ]]; then mode=Immediate; else mode=WaitForFirstConsumer; fi
     echo "{\\"metadata\\":{\\"name\\":\\"test-sc\\"},\\"volumeBindingMode\\":\\"$mode\\"}"
   else echo '{"items":[{"metadata":{"name":"test-sc","annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}]}'; fi;;
 'get pvc')
   if [[ "$*" == *'--ignore-not-found'* ]]; then
     [[ "$SCENARIO" == missing-pvc ]] && exit 0
     python3 -c 'import json,os; p={"metadata":{"uid":"current-pvc","annotations":{"demo.vllm/model-id":"Qwen/Qwen2.5-3B-Instruct","demo.vllm/model-revision":"aa8e72537993ba99e69dfaafa59ed015b17504d1"}},"spec":{"storageClassName":"test-sc"}}; a=p["metadata"]["annotations"]; a["demo.vllm/placement"]=json.dumps({"version":1,"nodeSelector":{"kubernetes.io/hostname":"gpu-a"},"tolerations":[{"key":"nvidia.com/gpu","operator":"Exists","effect":"NoSchedule"}],"storageClass":"test-sc","storageTopology":"auto"}); a.pop("demo.vllm/placement",None) if os.environ["SCENARIO"]=="legacy-pending" else None; a.update({"volume.kubernetes.io/selected-node":"other-gpu"}) if os.environ["SCENARIO"]=="legacy-pending" else None; print(json.dumps(p))'
     exit 0
   fi
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
 'get project'|'project audit-project'|'label namespace'|'annotate pvc') ;;
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

    def test_legacy_pending_pvc_is_not_assigned_a_different_placement(self):
        run, calls = self.run_setup('legacy-pending')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('new project', run.stderr)
        self.assertNotIn('annotate pvc', calls)
        self.assertNotIn('apply -f', calls)


if __name__ == "__main__":
    unittest.main()
