#!/usr/bin/env python3
"""Exercise unmodified lab scripts against local fault fixtures, never a cluster.

Usage: python3 failure-probes.py /path/to/vllm-local-demo/rhoai
Requires bash, curl, jq, bc, and standard Unix utilities.
Writes a JSON evidence document to stdout. A detected regression returns 1.
"""
import argparse
import contextlib
import http.server
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading


def intended_failure(name, returncode, stdout, stderr):
    """A broken fixture or an uncaught exception must never count as a pass."""
    output = stdout + stderr
    if returncode in (0, 99) or "Unexpected fixture command" in output or "Traceback" in output:
        return False
    if name in ("real_http_500_benchmark", "real_connection_refused_benchmark", "real_http_500_single"):
        try:
            data = json.loads(stdout)
            count = 1 if name.endswith("single") else 4
            summary = data["summary"]
            records = data["requests"]
            expected_error = "Connection refused" if "refused" in name else "HTTP 500"
            return (summary["attempted_requests"] == count
                    and summary["successful_requests"] == 0
                    and summary["failed_requests"] == count
                    and summary["output_tokens_per_second"] == 0
                    and len(records) == count
                    and all(not r["success"] and expected_error in r["error"] for r in records))
        except (ValueError, KeyError, TypeError):
            return False
    if name == "real_http_500_metrics":
        return "HTTP Error 500" in stderr
    if name == "mock_forbidden_cleanup":
        return "Forbidden (injected)" in stderr and "Cleanup complete" not in stdout
    if name == "mock_completed_job_with_changed_model":
        return "different or unrecorded model/revision" in stderr
    return False


class FailureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":{"message":"Injected HTTP 500"}}')

    do_POST = do_GET

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rhoai", type=Path)
    args = parser.parse_args()
    results = []
    with tempfile.TemporaryDirectory(prefix="vllm-audit-fixture-") as scratch:
        root = Path(scratch)
        # These are the only substituted tools. curl is the real installed binary.
        fixtures = {
            "oc": '''#!/bin/bash
case "$1 $2" in
  'whoami ') echo audit-user; exit 0;;
  'get nodes') echo '{"items":[{"metadata":{"name":"gpu-a","labels":{"kubernetes.io/hostname":"gpu-a","nvidia.com/gpu.present":"true","nvidia.com/gpu.compute.major":"8","nvidia.com/gpu.compute.minor":"9"}},"spec":{},"status":{"allocatable":{"nvidia.com/gpu":"1"},"conditions":[{"type":"Ready","status":"True"}]}}]}'; exit 0;;
  'get pods') echo '{"items":[]}'; exit 0;;
  'create --dry-run=client') echo '{"spec":{"predictor":{"model":{"resources":{"requests":{"nvidia.com/gpu":"1"},"limits":{"nvidia.com/gpu":"1"}}}}}}'; exit 0;;
  'get storageclass')
    if [[ "$3" == test-sc ]]; then echo '{"metadata":{"name":"test-sc"},"volumeBindingMode":"WaitForFirstConsumer"}'
    else echo '{"items":[{"metadata":{"name":"test-sc","annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}]}'; fi
    exit 0;;
  'get pod') echo pod/qwen25-3b-audit-fixture; exit 0;;
  'get pvc')
    if [[ "$*" == *'--ignore-not-found'* ]]; then
      python3 -c 'import json;print(json.dumps({"metadata":{"annotations":{"demo.vllm/placement":json.dumps({"version":1,"nodeSelector":{"kubernetes.io/hostname":"gpu-a"},"tolerations":[{"key":"nvidia.com/gpu","operator":"Exists","effect":"NoSchedule"}],"storageClass":"test-sc","storageTopology":"auto"})}},"spec":{"storageClassName":"test-sc"}}))'
      exit 0
    fi
    [[ "$*" == *model-id* ]] && echo Qwen/Qwen2.5-3B-Instruct
    [[ "$*" == *model-revision* ]] && echo aa8e72537993ba99e69dfaafa59ed015b17504d1
    exit 0;;
  'get project'|'project vllm-audit-fixture'|'label namespace') exit 0;;
  'get job') [[ "$*" == *jsonpath* ]] && echo 1; exit 0;;
  'delete project') echo 'Error: Forbidden (injected)' >&2; exit 1;;
esac
echo "Unexpected fixture command: $*" >&2
exit 99
''',
            "pgrep": "#!/bin/bash\nexit 0\n",
            "pkill": "#!/bin/bash\nexit 1\n",
        }
        for name, content in fixtures.items():
            target = root / name
            target.write_text(content, encoding="utf-8")
            target.chmod(0o700)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FailureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with contextlib.closing(socket.socket()) as unused:
            unused.bind(("127.0.0.1", 0))
            closed_port = unused.getsockname()[1]

        env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
               "INGRESS_HOST": "127.0.0.1", "INGRESS_PORT": str(server.server_port),
               "BASE_URL": f"http://127.0.0.1:{server.server_port}", "WARMUP_REQUESTS": "0",
               "HF_TOKEN": "", "TMPDIR": scratch,
               "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}

        def probe(name, script, arguments, stdin=None, overrides=None):
            run = subprocess.run(["bash", str(args.rhoai / script), *arguments],
                                 env={**env, **(overrides or {})}, input=stdin,
                                 text=True, capture_output=True, timeout=30)
            output = re.sub(r"\x1b\[[0-9;]*m", "", run.stdout + run.stderr)
            verified = intended_failure(name, run.returncode, run.stdout, run.stderr)
            results.append({"test": name, "script": script,
                            "arguments": arguments, "exit_code": run.returncode,
                            "expected": "nonzero exit with evidence of the intended injected failure",
                            "observed_false_success": run.returncode == 0,
                            "intended_failure_verified": verified,
                            "output": output})

        try:
            probe("real_http_500_benchmark", "04-run-inference.sh", ["benchmark", "2", "4"])
            probe("real_connection_refused_benchmark", "04-run-inference.sh",
                  ["benchmark", "2", "4"], overrides={"INGRESS_PORT": str(closed_port),
                  "BASE_URL": f"http://127.0.0.1:{closed_port}"})
            probe("real_http_500_single", "04-run-inference.sh", ["single"])
            probe("real_http_500_metrics", "04-run-inference.sh", ["metrics"])
            probe("mock_forbidden_cleanup", "05-cleanup.sh", ["vllm-audit-fixture"], "y\n")
            probe("mock_completed_job_with_changed_model", "01-setup-project.sh",
                  ["vllm-audit-fixture"], overrides={"MODEL_ID": "audit/different-model",
                  "MODEL_REVISION": "a" * 40})
        finally:
            server.shutdown()
            server.server_close()
    print(json.dumps({"fixture": "local HTTP server and mock oc/pgrep/pkill; no cluster access",
                      "results": results}, indent=2, ensure_ascii=False))
    return 1 if any(not item["intended_failure_verified"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
