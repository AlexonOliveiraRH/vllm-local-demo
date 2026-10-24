"""Real urllib redirect/error handling over synthetic transport; no sockets or credentials."""
from email.message import Message
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import urllib.response

SPEC = importlib.util.spec_from_file_location(
    "redirect_client", Path(__file__).resolve().parents[1] / "inference_client.py")
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)

ORIGIN = "https://origin.invalid"
DESTINATIONS = (ORIGIN + "/destination", "https://other.invalid/destination",
                "http://other.invalid/destination")
CODES = (301, 302, 303, 307, 308)
TOKEN = "Bearer synthetic-redirect-test-token"
TIMEOUT = 2.75
METRICS = b"vllm:prefix_cache_queries_total 200\nvllm:prefix_cache_hits_total 100\n"


def completion_body(stream):
    usage = {"prompt_tokens": 14, "completion_tokens": 32}
    if stream:
        chunks = [{"choices": [{"text": "Synthetic answer"}]},
                  {"choices": [], "usage": usage}]
        return b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n"
                        for chunk in chunks) + b"data: [DONE]\n\n"
    return json.dumps({"choices": [{"text": "Synthetic answer"}],
                       "usage": usage}).encode()


class RedirectTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.respond = lambda req: (200, {}, METRICS)
        environment = patch.dict(os.environ, {
            "API_KEY": "synthetic-redirect-test-token", "IGNORE_EOS": "1",
            "PREFIX_METRICS_TIMEOUT": "1",
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

        def transport(handler, connection_class, req, **kwargs):
            self.calls.append({"url": req.full_url, "method": req.get_method(),
                               "data": req.data, "timeout": req.timeout,
                               "authorization": req.get_header("Authorization"),
                               "content_type": req.get_header("Content-type")})
            code, headers, body = self.respond(req)
            message = Message()
            for key, value in headers.items():
                message[key] = value
            response = urllib.response.addinfourl(BytesIO(body), message, req.full_url, code)
            response.msg = "Synthetic HTTP response"
            return response

        # Keep request preparation, opener dispatch, redirects and HTTP errors real.
        transport_patch = patch.object(urllib.request.AbstractHTTPHandler, "do_open", transport)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)

    def completion(self, base_url=ORIGIN, stream=False):
        return CLIENT.request(base_url, "qwen25-3b", "Synthetic prompt", 32, TIMEOUT, stream)

    def redirect(self, path, code, destination, body):
        self.calls.clear()
        self.respond = lambda req: ((code, {"Location": destination}, b"")
                                    if req.full_url == ORIGIN + path else (200, {}, body))

    def assert_origin_only(self, path, method):
        # A second request is forbidden even if it drops the bearer or changes POST to GET.
        self.assertEqual([call["url"] for call in self.calls], [ORIGIN + path])
        self.assertEqual(self.calls[0]["method"], method)
        self.assertEqual(self.calls[0]["authorization"], TOKEN)
        self.assertEqual(self.calls[0]["timeout"], TIMEOUT)

    def test_completion_redirects_never_rewrite_or_forward_post_and_bearer(self):
        for code in CODES:
            for destination in DESTINATIONS:
                for stream in (False, True):
                    with self.subTest(code=code, destination=destination, stream=stream):
                        self.redirect("/v1/completions", code, destination, completion_body(stream))
                        result = self.completion(stream=stream)
                        self.assert_origin_only("/v1/completions", "POST")
                        self.assertFalse(result["success"])
                        self.assertEqual(result["http_status"], code)
                        self.assertEqual(result["error"], f"HTTP {code}")
                        self.assertEqual(result["completion_tokens"], 0)
                        self.assertGreaterEqual(result["latency_seconds"], 0)

    def test_metrics_redirects_never_forward_get_and_bearer(self):
        for code in CODES:
            for destination in DESTINATIONS:
                with self.subTest(code=code, destination=destination):
                    self.redirect("/metrics", code, destination, METRICS)
                    error = None
                    try:
                        CLIENT.metrics(ORIGIN, TIMEOUT)
                    except urllib.error.HTTPError as caught:
                        error = caught
                        caught.close()
                    self.assert_origin_only("/metrics", "GET")
                    self.assertIsNotNone(error)
                    self.assertEqual(error.code, code)

    def test_direct_http_and_https_completions_preserve_payload_auth_and_timeout(self):
        for base_url in ("http://127.0.0.1:8080", ORIGIN):
            for stream in (False, True):
                with self.subTest(base_url=base_url, stream=stream):
                    self.calls.clear()
                    self.respond = lambda req: (200, {}, completion_body(stream))
                    result = self.completion(base_url, stream)
                    self.assertTrue(result["success"], result)
                    self.assertEqual(result["http_status"], 200)
                    self.assertEqual(result["text"], "Synthetic answer")
                    self.assertEqual((result["prompt_tokens"], result["completion_tokens"]), (14, 32))
                    if stream:
                        self.assertGreaterEqual(result["ttft_seconds"], 0)
                        self.assertGreaterEqual(result["tpot_seconds"], 0)
                    else:
                        self.assertIsNone(result["ttft_seconds"])
                        self.assertIsNone(result["tpot_seconds"])
                    self.assertEqual(len(self.calls), 1)
                    call = self.calls[0]
                    self.assertEqual(call["url"], base_url + "/v1/completions")
                    self.assertEqual(call["method"], "POST")
                    self.assertEqual(call["authorization"], TOKEN)
                    self.assertEqual(call["timeout"], TIMEOUT)
                    self.assertEqual(call["content_type"], "application/json")
                    payload = {"model": "qwen25-3b", "prompt": "Synthetic prompt",
                               "max_tokens": 32, "temperature": 0, "seed": 0,
                               "stream": stream, "ignore_eos": True}
                    if stream:
                        payload["stream_options"] = {"include_usage": True}
                    self.assertEqual(json.loads(call["data"]), payload)

    def test_direct_http_and_https_metrics_preserve_auth_timeout_and_content(self):
        for base_url in ("http://127.0.0.1:8080", ORIGIN):
            with self.subTest(base_url=base_url):
                self.calls.clear()
                self.assertEqual(CLIENT.metrics(base_url, TIMEOUT), METRICS.decode())
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0], {
                    "url": base_url + "/metrics", "method": "GET", "data": None,
                    "timeout": TIMEOUT, "authorization": TOKEN,
                    "content_type": "application/json"})

    def test_redirected_completions_do_not_count_as_successful_throughput(self):
        self.respond = lambda req: (200, {}, completion_body(False))
        success = self.completion()
        self.redirect("/v1/completions", 302, DESTINATIONS[-1], completion_body(False))
        failure = self.completion()
        summary = CLIENT.summarize([success, failure], 4.0)
        self.assertEqual(summary["attempted_requests"], 2)
        self.assertEqual(summary["successful_requests"], 1)
        self.assertEqual(summary["failed_requests"], 1)
        self.assertEqual(summary["output_tokens"], 32)
        self.assertEqual(summary["output_tokens_per_second"], 8.0)
        self.assertEqual(summary["requests_per_second"], 0.25)

    def test_redirected_prefix_metrics_cannot_establish_cache_reuse(self):
        for redirect_fetch in (1, 2):
            with self.subTest(phase="baseline" if redirect_fetch == 1 else "poll"):
                self.calls.clear()
                fetches = 0

                def respond(req):
                    nonlocal fetches
                    if req.full_url == ORIGIN + "/v1/completions":
                        return 200, {}, completion_body(True)
                    if req.full_url == ORIGIN + "/metrics":
                        fetches += 1
                        if fetches == redirect_fetch:
                            return 302, {"Location": DESTINATIONS[-1]}, b""
                    body = (b"vllm:prefix_cache_queries_total 0\nvllm:prefix_cache_hits_total 0\n"
                            if fetches == 1 else METRICS)
                    return 200, {}, body

                self.respond = respond
                result = CLIENT.prefix_experiment(ORIGIN, "qwen25-3b", "Synthetic prompt", 32, TIMEOUT)
                self.assertNotIn(DESTINATIONS[-1], [call["url"] for call in self.calls])
                self.assertFalse(result["cache_reuse_observed"])
                self.assertEqual(result["prefix_metrics"]["status"], "unavailable")
                self.assertEqual(result["prefix_counter_delta"], {"queries": None, "hits": None})
                self.assertTrue(result["cold"]["success"] and result["warm"]["success"])

    def test_direct_errors_and_invalid_metrics_keep_existing_semantics(self):
        self.respond = lambda req: (500, {}, b"Synthetic server failure")
        result = self.completion()
        self.assertFalse(result["success"])
        self.assertEqual(result["http_status"], 500)
        self.assertEqual(result["error"], "HTTP 500")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            CLIENT.metrics(ORIGIN, TIMEOUT)
        self.assertEqual(raised.exception.code, 500)
        raised.exception.close()
        self.respond = lambda req: (200, {}, b"not a metrics endpoint")
        with self.assertRaises(ValueError):
            CLIENT.metrics(ORIGIN, TIMEOUT)

    def test_rejected_redirect_response_is_closed_before_propagation(self):
        self.redirect("/metrics", 302, DESTINATIONS[-1], METRICS)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            CLIENT.metrics(ORIGIN, TIMEOUT)
        try:
            self.assertTrue(raised.exception.closed)
            self.assertTrue(raised.exception.fp.closed)
        finally:
            raised.exception.close()


if __name__ == "__main__":
    unittest.main()
