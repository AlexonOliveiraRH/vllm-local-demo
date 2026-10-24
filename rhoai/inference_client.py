#!/usr/bin/env python3
"""Failure-aware vLLM lab client; not a substitute for a load generator."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid


def headers():
    result = {"Content-Type": "application/json"}
    if os.environ.get("API_KEY"):
        result["Authorization"] = "Bearer " + os.environ["API_KEY"]
    return result


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        error = urllib.error.HTTPError(req.full_url, code, "HTTP redirects are not allowed", headers, fp)
        error.close()
        raise error


def open_request(req, timeout):
    """Use the final endpoint without forwarding credentials or rewriting requests."""
    return urllib.request.build_opener(RejectRedirects()).open(req, timeout=timeout)


def request(base_url, model, prompt, max_tokens, timeout, stream=False):
    """Return a validated completion, including errors instead of hiding them."""
    started = time.perf_counter()
    result = {"success": False, "http_status": None, "completion_tokens": 0,
              "prompt_tokens": 0, "ttft_seconds": None, "tpot_seconds": None}
    payload = {"model": model, "prompt": prompt, "max_tokens": max_tokens,
               "temperature": 0, "seed": 0, "stream": stream}
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if os.environ.get("IGNORE_EOS", "0") == "1":
        payload["ignore_eos"] = True
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/completions",
                                 data=json.dumps(payload).encode(), headers=headers())
    try:
        with open_request(req, timeout) as response:
            result["http_status"] = response.status
            if stream:
                pieces, usage, first, last, done = [], None, None, None, False
                for line in response:
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        done = True
                        break
                    chunk = json.loads(data)
                    if not isinstance(chunk, dict):
                        raise ValueError("stream event must be a JSON object")
                    if chunk.get("error"):
                        raise ValueError("stream returned an error")
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list):
                        raise ValueError("stream choices must be an array")
                    for choice in choices:
                        if not isinstance(choice, dict) or not isinstance(choice.get("text", ""), str):
                            raise ValueError("invalid streaming choice")
                        text = choice.get("text", "")
                        if text:
                            now = time.perf_counter()
                            first = first if first is not None else now
                            last = now
                            pieces.append(text)
                if not done or first is None:
                    raise ValueError("incomplete or empty stream")
                answer = "".join(pieces)
                result["ttft_seconds"] = first - started
            else:
                body = json.load(response)
                if not isinstance(body, dict):
                    raise ValueError("response must be a JSON object")
                choices = body.get("choices")
                if (body.get("error") or not isinstance(choices, list) or not choices
                        or not isinstance(choices[0], dict) or not isinstance(choices[0].get("text"), str)):
                    raise ValueError("missing completion in response")
                answer, usage = choices[0]["text"], body.get("usage")
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                for k in ("prompt_tokens", "completion_tokens")):
                raise ValueError("missing or invalid token usage")
            if not answer or usage["completion_tokens"] == 0:
                raise ValueError("empty completion")
            result.update(success=True, text=answer, **{k: usage[k] for k in ("prompt_tokens", "completion_tokens")})
            if stream and usage["completion_tokens"] > 1:
                result["tpot_seconds"] = (last - first) / (usage["completion_tokens"] - 1)
    except urllib.error.HTTPError as exc:
        result.update(http_status=exc.code, error=f"HTTP {exc.code}")
        exc.close()
    except (HTTPException, OSError, ValueError, KeyError, TypeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["latency_seconds"] = time.perf_counter() - started
    return result


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {"mean": statistics.mean(values), "min": values[0], "max": values[-1],
            **{f"p{p}": values[max(0, math.ceil(p / 100 * len(values)) - 1)] for p in (50, 95, 99)}}


def summarize(results, elapsed):
    good = [r for r in results if r["success"]]
    output_tokens = sum(r["completion_tokens"] for r in good)
    return {"attempted_requests": len(results), "successful_requests": len(good),
            "failed_requests": len(results) - len(good), "wall_seconds": elapsed,
            "requests_per_second": len(good) / elapsed if elapsed > 0 else 0,
            "output_tokens": output_tokens,
            "output_tokens_per_second": output_tokens / elapsed if elapsed > 0 else 0,
            "latency_seconds": distribution([r["latency_seconds"] for r in good]),
            "ttft_seconds": distribution([r["ttft_seconds"] for r in good if r.get("ttft_seconds") is not None]),
            "tpot_seconds": distribution([r["tpot_seconds"] for r in good if r.get("tpot_seconds") is not None])}


def metrics(base_url, timeout):
    req = urllib.request.Request(base_url.rstrip("/") + "/metrics", headers=headers())
    with open_request(req, timeout) as response:
        text = response.read().decode()
    if "vllm:" not in text:
        raise ValueError("endpoint did not return vLLM metrics")
    return text


def cache_counts(text):
    """Return totals (None means absent) and individual series for reset checks."""
    result = {"queries": None, "hits": None}
    names = {"vllm:prefix_cache_" + key + "_total": key for key in result}
    series = {}
    for line in text.splitlines():
        line = line.strip()
        metric = re.match(r"[a-zA-Z_:][a-zA-Z0-9_:]*", line)
        if not metric or metric[0] not in names:
            continue
        sample = re.fullmatch(
            r'(?:\{(.*)\})?\s+([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)'
            r'(?:\s+[+-]?[0-9]+)?', line[metric.end():])
        if not sample:
            raise ValueError("Malformed prefix counter sample")
        labels = {}
        remaining = (sample[1] or "").strip()
        while remaining:
            label = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\[\\n"])*)"\s*(?:,\s*|$)', remaining)
            if not label or label[1] in labels:
                raise ValueError("Malformed or duplicate prefix counter label")
            labels[label[1]] = label[2]
            remaining = remaining[label.end():]
        name = names[metric[0]]
        key = (name, tuple(sorted(labels.items())))
        value = float(sample[2])
        if not math.isfinite(value) or value < 0 or key in series:
            raise ValueError("Invalid or duplicate prefix counter sample")
        series[key] = value
        result[name] = (result[name] or 0.0) + value
        if not math.isfinite(result[name]):
            raise ValueError("Nonfinite prefix counter total")
    if (result["queries"] is not None and result["hits"] is not None
            and result["hits"] > result["queries"]):
        raise ValueError("Prefix counter hit total exceeds query total")
    return result, series


def prefix_experiment(base_url, model, prompt, max_tokens, timeout):
    """Observe shared counters, without attributing their changes to our requests."""
    poll_timeout = float(os.environ.get("PREFIX_METRICS_TIMEOUT", "10"))
    if not math.isfinite(poll_timeout) or not 0 < poll_timeout <= 60:
        raise ValueError("PREFIX_METRICS_TIMEOUT must be greater than 0 and at most 60 seconds")
    unknown = {"queries": None, "hits": None}
    evidence = {"status": "unavailable", "before": unknown.copy(), "after": unknown.copy(),
                "polls": 0, "timeout_seconds": poll_timeout, "elapsed_seconds": 0.0}
    output = {"mode": "prefix", "model": model, "prefix_counter_delta": unknown.copy(),
              "cache_reuse_observed": False, "prefix_metrics": evidence,
              "interpretation": "Counters are shared by the server: positive deltas do not establish "
                                "that these requests caused reuse. Avoid other traffic. Two timings "
                                "are illustrative, not a causal or statistical speedup guarantee."}
    try:
        before, previous = cache_counts(metrics(base_url, timeout))
        evidence["before"] = before
    except ValueError as error:
        evidence.update(status="invalid", error=str(error))
    except (HTTPException, OSError) as error:
        evidence["error"] = "Baseline metrics fetch failed: " + type(error).__name__
    # Keep inference evidence even when counters cannot validate the experiment.
    output["cold"] = request(base_url, model, prompt, max_tokens, timeout, True)
    output["warm"] = request(base_url, model, prompt, max_tokens, timeout, True)
    if "error" in evidence:
        return output
    if any(value is None for value in before.values()):
        evidence["error"] = "Both prefix counters must be present before the requests; absent is not zero"
        return output
    if not (output["cold"]["success"] and output["warm"]["success"]):
        evidence.update(status="request_failed", error="Both streaming requests must succeed")
        return output

    started = time.monotonic()
    deadline = started + poll_timeout
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            evidence["polls"] += 1
            after, current = cache_counts(metrics(base_url, min(timeout, remaining)))
            evidence["after"] = after
            if time.monotonic() > deadline:
                evidence.update(status="not_observed", error="Metrics arrived after the polling deadline")
                output["prefix_counter_delta"] = unknown.copy()
                break
            if current.keys() - previous.keys():
                raise ValueError("Prefix counter series changed during observation")
            if any(current[key] < previous[key] for key in current):
                raise ValueError("Prefix counter decreased or reset during observation")
            # Track partial scrapes too: a reset cannot be hidden by a missing peer.
            previous.update(current)
            if any(value is None for value in after.values()):
                evidence["status"] = "unavailable"
                output["prefix_counter_delta"] = unknown.copy()
            else:
                if current.keys() != previous.keys():
                    raise ValueError("Prefix counter series changed during observation")
                delta = {key: after[key] - before[key] for key in before}
                if delta["hits"] > delta["queries"]:
                    raise ValueError("Prefix counter hit delta exceeds query delta")
                output["prefix_counter_delta"] = delta
                if delta["queries"] > 0 and delta["hits"] > 0:
                    evidence["status"] = "observed"
                    output["cache_reuse_observed"] = True
                    return output
                evidence["status"] = "not_observed"
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.25, remaining))
        evidence.setdefault("error", "Positive hit and query deltas were not observed before the polling deadline")
    except ValueError as error:
        evidence.update(status="invalid", error=str(error))
        output["prefix_counter_delta"] = unknown.copy()
    except (HTTPException, OSError) as error:
        evidence.update(status="unavailable", error="Metrics fetch failed: " + type(error).__name__)
        output["prefix_counter_delta"] = unknown.copy()
    finally:
        evidence["elapsed_seconds"] = time.monotonic() - started
    return output


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "single"
    base_url = os.environ.get("BASE_URL", "http://127.0.0.1:8080")
    model = os.environ.get("MODEL_NAME", "qwen25-3b")
    timeout = float(os.environ.get("REQUEST_TIMEOUT", "120"))
    max_tokens = int(os.environ.get("MAX_TOKENS", "128"))
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    warmup = int(os.environ.get("WARMUP_REQUESTS", "2"))
    if not math.isfinite(timeout) or min(timeout, max_tokens, concurrency, count) <= 0 or warmup < 0:
        raise ValueError("timeout, tokens, concurrency, and requests must be positive; warmup must be nonnegative")
    prompt = os.environ.get("PROMPT", "Explain three practical considerations for serving a language model to multiple users.")
    stream = os.environ.get("STREAM", "0") == "1"
    if mode == "metrics":
        print("Snapshot: token totals are counters, not tokens/second. Use rate() for throughput.")
        for line in metrics(base_url, timeout).splitlines():
            if line.startswith("vllm:") and any(key in line for key in ("cache", "num_requests", "tokens_total")):
                print(line)
        return 0
    if mode == "prefix":
        prompt = "Experiment " + uuid.uuid4().hex + ". " + (
            "The service receives concurrent requests and reuses attention state for matching prompt prefixes. " * 128
        ) + "Summarize the service in one sentence."
        output = prefix_experiment(base_url, model, prompt, max_tokens, timeout)
        success = output["cache_reuse_observed"]
    elif mode in ("single", "benchmark"):
        if mode == "benchmark":
            for _ in range(warmup):
                result = request(base_url, model, prompt, max_tokens, timeout, stream)
                if not result["success"]:
                    raise ValueError("warmup failed: " + result.get("error", "invalid response"))
        else:
            concurrency, count, warmup = 1, 1, 0
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(lambda _: request(base_url, model, prompt, max_tokens, timeout, stream), range(count)))
        summary = summarize(results, time.perf_counter() - started)
        output = {"mode": mode, "model": model, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                  "configuration": {"concurrency": concurrency, "requests": count, "warmup_requests": warmup,
                                    "max_tokens": max_tokens, "stream": stream,
                                    "ignore_eos": os.environ.get("IGNORE_EOS", "0") == "1", "prompt": prompt},
                  "summary": summary, "requests": results,
                  "notes": "Nearest-rank percentiles. Client-observed timing includes transport. Non-streaming latency is not TTFT. A small sample is not an SLO test."}
        success = summary["failed_requests"] == 0
    else:
        raise ValueError("mode must be single, benchmark, metrics, or prefix")
    encoded = json.dumps(output, indent=2)
    print(encoded)
    if os.environ.get("RESULTS_FILE"):
        with Path(os.environ["RESULTS_FILE"]).open("x", encoding="utf-8") as target:
            target.write(encoded + "\n")
    return 0 if success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
