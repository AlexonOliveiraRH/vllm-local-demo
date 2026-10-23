"""Regression tests for the actual HTTP client, using a loopback test server."""
import importlib.util
import json
from pathlib import Path
import threading
import subprocess
import os
import sys
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODULE = Path(__file__).resolve().parents[1] / "inference_client.py"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path.startswith("/error"):
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"injected failure"}')
            return
        if self.path.startswith("/partial-http"):
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{"choices":')
            self.close_connection = True
            return
        self.send_response(200)
        self.end_headers()
        if self.path.startswith("/stream-array"):
            self.wfile.write(b'data: []\n\ndata: [DONE]\n\n')
        elif self.path.startswith("/null-choice"):
            self.wfile.write(b'{"choices":[null]}')
        elif body.get("stream"):
            chunks = [{"choices": [{"text": "test answer"}]},
                      {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8}}]
            for chunk in chunks:
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            if not self.path.startswith("/truncated"):
                self.wfile.write(b"data: [DONE]\n\n")
        elif self.path.startswith("/malformed"):
            self.wfile.write(b'{"choices":[]}')
        else:
            self.wfile.write(json.dumps({"choices": [{"text": "test answer"}],
                                        "usage": {"prompt_tokens": 12,
                                                  "completion_tokens": 8}}).encode())

    def log_message(self, *args):
        pass


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("client", MODULE)
        cls.client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.client)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, suffix=""):
        return self.client.request(self.url + suffix, "model", "prompt", 8, 2, False)

    def test_http_500_is_not_a_successful_inference(self):
        result = self.request("/error")
        self.assertFalse(result["success"])
        self.assertEqual(result["http_status"], 500)

    def test_malformed_success_payload_is_rejected(self):
        self.assertFalse(self.request("/malformed")["success"])

    def test_valid_response_preserves_real_token_counts(self):
        result = self.request()
        self.assertTrue(result["success"])
        self.assertEqual(result["completion_tokens"], 8)
        self.assertEqual(result["text"], "test answer")
        self.assertIsNone(result["ttft_seconds"])

    def test_summary_counts_successes_not_attempts(self):
        success = {"success": True, "latency_seconds": 2.0,
                   "completion_tokens": 8, "prompt_tokens": 12,
                   "ttft_seconds": None, "tpot_seconds": None}
        failure = {"success": False, "latency_seconds": 0.01, "error": "failed"}
        summary = self.client.summarize([success, failure], 4.0)
        self.assertEqual(summary["successful_requests"], 1)
        self.assertEqual(summary["failed_requests"], 1)
        self.assertEqual(summary["requests_per_second"], 0.25)
        self.assertEqual(summary["output_tokens_per_second"], 2.0)
        self.assertEqual(summary["latency_seconds"]["p95"], 2.0)

    def test_all_failures_produce_zero_successful_throughput(self):
        summary = self.client.summarize([{"success": False}], 1.0)
        self.assertEqual(summary["requests_per_second"], 0)
        self.assertIsNone(summary["latency_seconds"])

    def test_streaming_counts_usage_and_measures_first_text(self):
        result = self.client.request(self.url, "model", "prompt", 8, 2, True)
        self.assertTrue(result["success"])
        self.assertEqual(result["completion_tokens"], 8)
        self.assertGreater(result["ttft_seconds"], 0)

    def test_truncated_stream_is_failure(self):
        result = self.client.request(self.url + "/truncated", "model", "prompt", 8, 2, True)
        self.assertFalse(result["success"])

    def test_null_choice_is_a_recorded_failure(self):
        self.assertFalse(self.request("/null-choice")["success"])

    def test_nonobject_stream_event_is_a_recorded_failure(self):
        result = self.client.request(self.url + "/stream-array", "model", "prompt", 8, 2, True)
        self.assertFalse(result["success"])

    def test_incomplete_http_body_is_a_recorded_failure(self):
        self.assertFalse(self.request("/partial-http")["success"])

    def test_cli_writes_evidence_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            result_file = Path(folder) / "result.json"
            env = {**os.environ, "BASE_URL": self.url, "RESULTS_FILE": str(result_file),
                   "WARMUP_REQUESTS": "0"}
            command = [sys.executable, str(MODULE), "benchmark", "2", "4"]
            run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            saved = json.loads(result_file.read_text())
            self.assertEqual(saved["summary"]["successful_requests"], 4)
            self.assertEqual(saved["summary"]["output_tokens"], 32)
            repeat = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(repeat.returncode, 0)
            self.assertEqual(saved, json.loads(result_file.read_text()))

    def test_cli_rejects_zero_concurrency(self):
        run = subprocess.run([sys.executable, str(MODULE), "benchmark", "0", "4"],
                             capture_output=True, text=True, timeout=5)
        self.assertNotEqual(run.returncode, 0)


if __name__ == "__main__":
    unittest.main()
