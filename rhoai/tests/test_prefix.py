"""Prefix evidence regressions; unit polling uses a clock with no real sleeps."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prefix_client", ROOT / "inference_client.py")
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


def counters(queries=0, hits=0, labels='model_name="qwen25-3b",engine="0"'):
    return (f'vllm:prefix_cache_queries_total{{{labels}}} {queries}\n'
            f'vllm:prefix_cache_hits_total{{{labels}}} {hits}\n')


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class PrefixTests(unittest.TestCase):
    def run_prefix(self, snapshots, *, failed_request=False, timeout="1", fetch_delay=0):
        clock = Clock()
        pending = iter(snapshots)
        last = snapshots[-1]
        fetch_timeouts = []

        def scrape(base_url, request_timeout):
            nonlocal last
            fetch_timeouts.append(request_timeout)
            last = next(pending, last)
            if len(fetch_timeouts) > 1:
                clock.now += fetch_delay
            if isinstance(last, Exception):
                raise last
            return last

        answer = {"success": not failed_request, "http_status": 200,
                  "text": "test answer", "prompt_tokens": 1200, "completion_tokens": 8,
                  "ttft_seconds": 0.01, "tpot_seconds": 0.001, "latency_seconds": 0.02}
        output = io.StringIO()
        with patch.dict(os.environ, {"PREFIX_METRICS_TIMEOUT": timeout}, clear=True), \
                patch.object(CLIENT.sys, "argv", ["inference_client.py", "prefix"]), \
                patch.object(CLIENT, "metrics", side_effect=scrape), \
                patch.object(CLIENT, "request", return_value=answer), \
                patch.object(CLIENT.time, "monotonic", clock.monotonic), \
                patch.object(CLIENT.time, "sleep", clock.sleep), \
                contextlib.redirect_stdout(output):
            status = CLIENT.main()
        return status, json.loads(output.getvalue()), clock.now, fetch_timeouts

    def test_missing_is_unavailable_not_observed_zero(self):
        for missing in ("vllm:num_requests_running 0\n",
                        'vllm:prefix_cache_queries_total 0\n',
                        'vllm:prefix_cache_hits_total 0\n'):
            with self.subTest(missing=missing):
                status, result, _, _ = self.run_prefix([missing, counters(200, 100)])
                self.assertNotEqual(status, 0)
                self.assertFalse(result["cache_reuse_observed"])
                self.assertEqual(result["prefix_metrics"]["status"], "unavailable")
                self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})

    def test_observed_zero_is_preserved_but_does_not_pass(self):
        status, result, elapsed, timeouts = self.run_prefix([counters(), counters()])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "not_observed")
        self.assertEqual(result["prefix_counter_delta"], {"queries": 0, "hits": 0})
        self.assertEqual(result["prefix_metrics"]["before"], {"queries": 0, "hits": 0})
        self.assertFalse(result["cache_reuse_observed"])
        self.assertGreater(elapsed, 0)
        self.assertLessEqual(elapsed, 1)
        self.assertTrue(all(0 < value <= 1 for value in timeouts[1:]))

    def test_positive_hits_and_queries_are_both_required(self):
        for after in (counters(200, 0), counters(0, 100)):
            with self.subTest(after=after):
                status, result, _, _ = self.run_prefix([counters(), after])
                self.assertNotEqual(status, 0)
                self.assertFalse(result["cache_reuse_observed"])

    def test_hit_delta_exceeding_query_delta_is_invalid(self):
        # Both snapshots are coherent; only their delta is impossible (1, 100).
        status, result, _, _ = self.run_prefix([counters(100, 0), counters(101, 100)])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "invalid")
        self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})
        self.assertFalse(result["cache_reuse_observed"])

    def test_incoherent_baseline_and_scraped_totals_are_invalid(self):
        for snapshots in ([counters(1, 100), counters(201, 200)],
                          [counters(), counters(1, 100)]):
            with self.subTest(snapshots=snapshots):
                status, result, _, _ = self.run_prefix(snapshots)
                self.assertNotEqual(status, 0)
                self.assertEqual(result["prefix_metrics"]["status"], "invalid")
                self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})
                self.assertFalse(result["cache_reuse_observed"])
                self.assertTrue(result["cold"]["success"] and result["warm"]["success"])

    def test_parser_rejects_hit_totals_exceeding_queries(self):
        with self.assertRaises(ValueError):
            CLIENT.cache_counts(counters(1, 100))

    def test_equal_positive_hit_and_query_deltas_remain_valid(self):
        for before, after in ((counters(), counters(1, 1)),
                              (counters(100, 50), counters(200, 150)),
                              (counters(100, 100), counters(200, 200))):
            with self.subTest(before=before, after=after):
                status, result, _, _ = self.run_prefix([before, after])
                self.assertEqual(status, 0)
                self.assertTrue(result["cache_reuse_observed"])
                self.assertEqual(result["prefix_metrics"]["status"], "observed")

    def test_malformed_nonfinite_negative_and_duplicate_samples_are_rejected(self):
        bad_samples = [counters(value, 1) for value in
                       ("broken", "NaN", "+Inf", "-Inf", "1e999", "-1", "1_000")]
        bad_samples += [counters() + 'vllm:prefix_cache_hits_total{model_name="qwen25-3b",engine="0"} 2\n',
                        'vllm:prefix_cache_queries_total{model_name="broken} 1\n' + counters(),
                        counters(1, 1, 'engine="0",engine="1"'),
                        counters(1, 1) + 'vllm:prefix_cache_hits_total\n']
        for bad in bad_samples:
            for snapshots in ([bad, counters(200, 100)], [counters(), bad]):
                with self.subTest(snapshots=snapshots):
                    try:
                        status, result, _, _ = self.run_prefix(snapshots)
                    except ValueError as error:
                        self.fail(f"Invalid metrics must retain JSON evidence, not escape: {error}")
                    self.assertNotEqual(status, 0)
                    self.assertFalse(result["cache_reuse_observed"])
                    self.assertEqual(result["prefix_metrics"]["status"], "invalid")
                    self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})

    def test_timestamp_is_not_mistaken_for_sample_value(self):
        before = 'vllm:prefix_cache_queries_total 0 1700000000000\nvllm:prefix_cache_hits_total 0 1700000000000\n'
        after = 'vllm:prefix_cache_queries_total 200 1700000001000\nvllm:prefix_cache_hits_total 100 1700000001000\n'
        status, result, _, _ = self.run_prefix([before, after])
        self.assertEqual(status, 0)
        self.assertEqual(result["prefix_counter_delta"], {"queries": 200, "hits": 100})

    def test_metric_name_suffixes_and_external_cache_are_not_local_counters(self):
        wrong = ('vllm:prefix_cache_queries_total_created 200\n'
                 'vllm:prefix_cache_hits_total_created 100\n'
                 'vllm:external_prefix_cache_queries_total 200\n'
                 'vllm:external_prefix_cache_hits_total 100\n')
        status, result, _, _ = self.run_prefix([wrong, wrong])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "unavailable")

    def test_reset_is_rejected_even_when_another_series_masks_the_total(self):
        for before, after in (
            (counters(100, 50), counters(90, 60)),
            (counters(100, 50), counters(110, 40)),
            (counters(100, 50, 'engine="0"') + counters(100, 50, 'engine="1"'),
             counters(90, 40, 'engine="0"') + counters(200, 100, 'engine="1"')),
        ):
            with self.subTest(after=after):
                status, result, _, _ = self.run_prefix([before, after])
                self.assertNotEqual(status, 0)
                self.assertFalse(result["cache_reuse_observed"])
                self.assertEqual(result["prefix_metrics"]["status"], "invalid")
                self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})

    def test_reset_between_polls_cannot_hide_behind_the_baseline(self):
        status, result, _, _ = self.run_prefix([counters(10, 0), counters(20, 0), counters(15, 5)])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "invalid")

    def test_partial_scrape_cannot_hide_a_decreasing_counter(self):
        for partial in ('vllm:prefix_cache_queries_total{engine="0"} 90\n',
                        'vllm:prefix_cache_queries_total{engine="0"} 300\n'):
            with self.subTest(partial=partial):
                status, result, _, _ = self.run_prefix([
                    counters(100, 50, 'engine="0"'), partial, counters(200, 100, 'engine="0"')])
                self.assertNotEqual(status, 0)
                self.assertEqual(result["prefix_metrics"]["status"], "invalid")
                self.assertFalse(result["cache_reuse_observed"])

    def test_delayed_publication_is_polled_until_positive_deltas(self):
        status, result, elapsed, _ = self.run_prefix(
            [counters(10, 5), counters(10, 5), counters(210, 105)])
        self.assertEqual(status, 0)
        self.assertTrue(result["cache_reuse_observed"])
        self.assertEqual(result["prefix_counter_delta"], {"queries": 200, "hits": 100})
        self.assertEqual(result["prefix_metrics"]["status"], "observed")
        self.assertGreater(elapsed, 0)
        self.assertLessEqual(elapsed, 1)

    def test_missing_post_request_sample_is_not_converted_to_zero(self):
        status, result, _, _ = self.run_prefix([counters(), "vllm:num_requests_running 0\n"])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "unavailable")
        self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})

    def test_reordered_labels_are_the_same_series(self):
        status, result, _, _ = self.run_prefix([
            counters(10, 5), counters(210, 105, 'engine="0",model_name="qwen25-3b"')])
        self.assertEqual(status, 0)
        self.assertEqual(result["prefix_counter_delta"], {"queries": 200, "hits": 100})

    def test_changed_series_cannot_prove_reuse(self):
        status, result, _, _ = self.run_prefix([
            counters(10, 5, 'engine="0"'), counters(210, 105, 'engine="1"')])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "invalid")

    def test_success_retains_existing_evidence_fields(self):
        status, result, elapsed, _ = self.run_prefix([counters(), counters(200, 100)])
        self.assertEqual(status, 0)
        self.assertTrue(result["cache_reuse_observed"])
        self.assertEqual(result["prefix_counter_delta"], {"queries": 200, "hits": 100})
        self.assertEqual(result["cold"]["completion_tokens"], 8)
        self.assertEqual(result["warm"]["completion_tokens"], 8)
        self.assertEqual(result["mode"], "prefix")
        self.assertEqual(result["model"], "qwen25-3b")
        self.assertIn("shared", result["interpretation"].lower())
        self.assertEqual(elapsed, 0)

    def test_inference_failure_cannot_pass_with_positive_counters(self):
        status, result, _, _ = self.run_prefix([counters(), counters(200, 100)], failed_request=True)
        self.assertNotEqual(status, 0)
        self.assertFalse(result["cache_reuse_observed"])

    def test_late_scrape_cannot_pass_after_poll_deadline(self):
        status, result, _, _ = self.run_prefix([counters(), counters(200, 100)], fetch_delay=2)
        self.assertNotEqual(status, 0)
        self.assertFalse(result["cache_reuse_observed"])

    def test_late_scrape_cannot_leave_stale_deltas_next_to_new_counts(self):
        status, result, _, _ = self.run_prefix(
            [counters(), counters(), counters(200, 100)], fetch_delay=0.6)
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})

    def test_fetch_errors_preserve_inference_and_mark_metrics_unavailable(self):
        for snapshots in ([OSError("offline")], [counters(), OSError("offline")]):
            with self.subTest(snapshots=snapshots):
                status, result, _, _ = self.run_prefix(snapshots)
                self.assertNotEqual(status, 0)
                self.assertEqual(result["prefix_metrics"]["status"], "unavailable")
                self.assertTrue(result["cold"]["success"] and result["warm"]["success"])

    def test_aggregate_overflow_is_rejected(self):
        overflowing = counters("1e308", 0, 'engine="0"') + counters("1e308", 0, 'engine="1"')
        status, result, _, _ = self.run_prefix([overflowing])
        self.assertNotEqual(status, 0)
        self.assertEqual(result["prefix_metrics"]["status"], "invalid")

    def test_invalid_poll_timeout_is_rejected_before_requests(self):
        for timeout in ("0", "-1", "nan", "inf", "61"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.run_prefix([counters(), counters(200, 100)], timeout=timeout)


class PrefixEndToEndTests(unittest.TestCase):
    def test_shell_http_streams_and_saved_evidence_without_cluster(self):
        class Handler(BaseHTTPRequestHandler):
            completed = 0
            scenario = "success"

            def do_GET(self):
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                if self.scenario == "missing":
                    body = b"vllm:num_requests_running 0\n"
                elif self.scenario == "reset":
                    body = (counters(90, 60) if self.completed == 2 else counters(100, 50)).encode()
                elif self.scenario == "malformed" and self.completed == 2:
                    body = counters("NaN", 100).encode()
                elif self.scenario == "incoherent_delta":
                    body = (counters(101, 100) if self.completed == 2 else counters(100, 0)).encode()
                elif self.scenario == "incoherent_baseline":
                    body = (counters(201, 200) if self.completed == 2 else counters(1, 100)).encode()
                else:
                    body = counters(200 if self.completed == 2 else 0,
                                    100 if self.completed == 2 else 0).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if (self.path != "/v1/completions" or not payload.get("stream")
                        or payload.get("stream_options") != {"include_usage": True}):
                    self.send_error(400)
                    return
                type(self).completed += 1
                body = (b'data: {"choices":[{"text":"test answer"}]}\n\n'
                        b'data: {"choices":[],"usage":{"prompt_tokens":1200,"completion_tokens":8}}\n\n'
                        b'data: [DONE]\n\n')
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            for scenario, expected_status in (("success", "observed"), ("missing", "unavailable"),
                                              ("reset", "invalid"), ("malformed", "invalid"),
                                              ("incoherent_delta", "invalid"),
                                              ("incoherent_baseline", "invalid")):
                with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                    Handler.scenario, Handler.completed = scenario, 0
                    result_file = Path(directory) / "prefix.json"
                    env = {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1",
                           "BASE_URL": f"http://127.0.0.1:{server.server_port}",
                           "RESULTS_FILE": str(result_file), "REQUEST_TIMEOUT": "2",
                           "PREFIX_METRICS_TIMEOUT": "1", "NO_PROXY": "127.0.0.1"}
                    run = subprocess.run(["bash", str(ROOT / "04-run-inference.sh"), "prefix"],
                                         env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(run.returncode, 0 if scenario == "success" else 1, run.stderr)
                    result = json.loads(run.stdout)
                    self.assertEqual(result, json.loads(result_file.read_text()))
                    self.assertIn("prefix_metrics", result)
                    self.assertEqual(result["prefix_metrics"]["status"], expected_status)
                    self.assertEqual(result["cache_reuse_observed"], scenario == "success")
                    self.assertEqual(result["prefix_counter_delta"],
                                     {"queries": 200, "hits": 100} if scenario == "success"
                                     else {"queries": None, "hits": None})
                    self.assertTrue(result["cold"]["success"] and result["warm"]["success"])
                    self.assertEqual(result["warm"]["completion_tokens"], 8)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
