"""Interactive demo helpers: loading behavior and output summaries."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

HELPERS = Path(__file__).resolve().parents[1] / "demo-helpers.sh"


def bash(script, stdin=""):
    return subprocess.run(["bash", "-c", script], input=stdin, capture_output=True, text=True, timeout=30)


class HelperTests(unittest.TestCase):
    def test_executing_instead_of_sourcing_explains_usage(self):
        run = subprocess.run(["bash", str(HELPERS)], capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertIn("source ./demo-helpers.sh", run.stderr)

    def test_sourcing_defines_helpers_and_enables_pipefail(self):
        run = bash(f'source "{HELPERS}" 2>/dev/null; '
                   'for f in show_deployment summary_single summary_bench summary_prefix; do type -t "$f"; done; '
                   'set -o | grep pipefail')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.split()[:4], ["function"] * 4)
        self.assertRegex(run.stdout, r"pipefail\s+on")

    def test_pipeline_reports_an_upstream_failure(self):
        run = bash(f'source "{HELPERS}" 2>/dev/null; false | cat')
        self.assertNotEqual(run.returncode, 0)

    def test_show_deployment_requires_a_project(self):
        run = bash(f'source "{HELPERS}" 2>/dev/null; unset PROJECT; show_deployment')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("Set PROJECT", run.stderr)

    @unittest.skipUnless(shutil.which("jq"), "jq is not installed")
    def test_summaries_round_the_key_fields(self):
        single = {"summary": {"successful_requests": 1, "attempted_requests": 1, "output_tokens": 32,
                              "latency_seconds": {"mean": 1.28123}, "output_tokens_per_second": 24.987}}
        bench = {"configuration": {"concurrency": 8},
                 "summary": {"successful_requests": 12, "attempted_requests": 12,
                             "output_tokens_per_second": 127.84, "latency_seconds": {"mean": 1.4567},
                             "ttft_seconds": {"mean": 0.6412}, "tpot_seconds": {"mean": 0.02649}}}
        prefix = {"cache_reuse_observed": True, "prefix_counter_delta": {"hits": 1952.0},
                  "cold": {"ttft_seconds": 0.6866, "tpot_seconds": 0.02628},
                  "warm": {"prompt_tokens": 1960, "ttft_seconds": 0.5109, "tpot_seconds": 0.02594}}
        cases = [
            ("summary_single", single, {"ok": "1/1", "tokens": 32, "latency_s": 1.28, "tok_per_s": 25}),
            ("summary_bench", bench, {"c": 8, "ok": "12/12", "tok_per_s": 127.8, "e2e_s": 1.46,
                                      "ttft_s": 0.64, "tpot_ms": 26}),
            ("summary_prefix", prefix, {"reuse": True, "prompt_tokens": 1960, "cached_tokens": 1952,
                                        "ttft_cold_s": 0.69, "ttft_warm_s": 0.51,
                                        "tpot_cold_ms": 26, "tpot_warm_ms": 26}),
        ]
        for name, record, expected in cases:
            with self.subTest(name):
                run = bash(f'source "{HELPERS}" 2>/dev/null; {name}', json.dumps(record))
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout), expected)


if __name__ == "__main__":
    unittest.main()
