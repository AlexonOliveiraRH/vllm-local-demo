# vLLM on Red Hat OpenShift AI

A reproducible GPU inference lab using KServe, a PVC-backed model, and the
OpenAI-compatible vLLM API. It's an educational lab, not a production security
or capacity-planning reference: the scripts don't configure public routes,
authentication policies, autoscaling, or monitoring dashboards.

## Tested configuration

| Component | Version or setting |
| --- | --- |
| OpenShift Container Platform | 4.22 |
| Red Hat OpenShift AI | 3.5 |
| GPU | One NVIDIA L4 (24 GB) |
| Runtime | vLLM 0.24.0 (OpenShift AI build) |
| Model | Qwen2.5-3B-Instruct, pinned revision below |
| Serving | KServe RawDeployment, BF16, eager execution, 4096-token context |
| Prefix caching | Enabled |

The scripts resolve the runtime from your installed template and select a
compatible GPU node automatically, so the lab adapts to other clusters. Check the
[OpenShift AI supported configurations](https://access.redhat.com/articles/rhoai-supported-configs)
and the [AI Inference version matrix](https://access.redhat.com/articles/rhaii-supported-configs)
when choosing your own stack.

## Prerequisites

- OpenShift AI 3.5 with KServe managed and an available NVIDIA GPU with compute
  capability 7.5 or newer. GPU Feature Discovery labels must be present on GPU
  nodes. Model memory and runtime-specific kernel requirements still apply.
- A storage class that can provide the requested PVC. The default class is used;
  set `STORAGE_CLASS` to select another. `WaitForFirstConsumer` is the safe choice
  for storage with node or zone restrictions. For shared storage with `Immediate`
  binding, set `STORAGE_TOPOLOGY=shared` after confirming that its volumes can be
  mounted from any GPU node.
- An authenticated `oc` client matching your OpenShift release, Bash, Python 3.9+,
  and `curl`. Get the client from your console's **Command Line Tools** page or the
  [OpenShift CLI download instructions](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/cli_tools/openshift-cli-oc).
  The scripts use only the Python standard library. [`jq`](https://jqlang.org/) is
  optional, for the output summaries shown in [Reading the output](#reading-the-output).
- Permissions to create projects and namespaced serving resources, plus read
  access to GPU nodes, Pods across namespaces (to check GPU allocation), storage
  classes, and the runtime template.
- Registry access for the runtime image, and cluster egress to the model hub and
  the Python package index. The download Job uses UBI Python 3.11 and installs
  `huggingface_hub==0.34.4`.

This lab uses KServe RawDeployment, so it doesn't need Knative. Follow the
prerequisites for your installed OpenShift AI release.

### Model license

The default is `Qwen/Qwen2.5-3B-Instruct`, pinned to commit
`aa8e72537993ba99e69dfaafa59ed015b17504d1`. Its
[Qwen Research License](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE)
grants research and evaluation use and directs commercial users to request a
separate license. This repository's software license doesn't cover model weights.
Review the model's terms before any commercial deployment.

## Quick start

Run from this directory after logging in to your cluster:

```bash
./00-run-lab.sh up vllm-demo
export PROJECT=vllm-demo
./04-run-inference.sh single
STREAM=1 RESULTS_FILE=benchmark-c4.json ./04-run-inference.sh benchmark 4 32
./04-run-inference.sh metrics
MAX_TOKENS=64 ./04-run-inference.sh prefix
```

`up` downloads the model, resolves the runtime image from the installed OpenShift
AI template, deploys, waits for model health, and runs one inference. The lab keeps
running afterward. The first download and image pull can take several minutes, so
prepare the lab before a live demonstration.

### Placement on different clusters

The scripts pick a GPU node that is Ready, has a supported GPU architecture, has
unallocated GPU capacity, and whose scheduling taints are tolerated. This is a
point-in-time check, not a reservation: CPU, memory, quotas, or other workloads
can still prevent scheduling.

Set only the overrides your cluster needs:

```bash
# Optional: limit selection to a GPU pool by node labels.
export GPU_NODE_SELECTOR='{"nvidia.com/gpu.product":"NVIDIA-L4"}'

# Optional: replace the default nvidia.com/gpu toleration with the tolerations
# allowed for your GPU pool. Don't bypass taints that reserve nodes for others.
export GPU_TOLERATIONS='[{"key":"nvidia.com/gpu","operator":"Exists","effect":"NoSchedule"},{"key":"example.com/gpu-pool","operator":"Exists","effect":"NoSchedule"}]'

# Optional: choose a non-default storage class.
export STORAGE_CLASS=your-storage-class

# Required only for Immediate binding on shared storage.
export STORAGE_TOPOLOGY=shared
```

The default toleration is `nvidia.com/gpu` with `Exists` and `NoSchedule`.
`GPU_NODE_SELECTOR` is a JSON map of exact label matches; `GPU_TOLERATIONS` is a
JSON array with explicit keys and effects. `STORAGE_ZONE` optionally filters by
the `topology.kubernetes.io/zone` label on clusters that have it.

The selected node and tolerations are saved on the model PVC, and both the download
Job and the predictor use them. Rerunning setup keeps that placement; use a new
project to move the lab to another node. The download Job uses CPU and memory
only; it doesn't request a GPU.

Results depend on the runtime build, GPU, and other workloads on the node. When
you move to a new cluster, run the lifecycle check below before a live demo.

`RESULTS_FILE` is optional and must not already exist. The JSON output includes
per-request status, token counts, errors, and timing. Review raw results for
sensitive prompts or generated content before sharing them.

### Automated lifecycle check

```bash
./00-run-lab.sh verify vllm-lifecycle-check
```

`verify` creates a new project (it refuses an existing one) and runs setup,
deployment, readiness, a single inference, benchmarks at concurrency 1, 4, and 8,
metrics, and the prefix-cache experiment. If everything passes, it deletes that
project. If a step fails, it exits nonzero and keeps the resources for inspection.

By default, `verify` uses streaming, 32 completion tokens, EOS ignored, and 12
measured requests per concurrency level, after two warmup requests. `MAX_TOKENS`
and `IGNORE_EOS` override the first two. Keep the same settings when comparing runs.

## Reading the output

The `single`, `benchmark`, and `prefix` modes each print one JSON document on
stdout. The `metrics` mode prints a text snapshot of Prometheus metrics.
For the JSON modes, `demo-helpers.sh` provides filters that summarize the results
with [`jq`](https://jqlang.org/). Load it into your Bash terminal with `source`
(running it as a script has no effect on your shell). It also turns on `pipefail`,
so a pipeline reports a failure if either the inference command or `jq` fails:

```bash
source ./demo-helpers.sh

export MAX_TOKENS=32 IGNORE_EOS=1
(
  set -e
  ./04-run-inference.sh single | summary_single
  for c in 1 4 8; do
    STREAM=1 ./04-run-inference.sh benchmark "$c" 12 | summary_bench
  done
  ./04-run-inference.sh prefix | summary_prefix
)
```

The subshell stops the sequence on a failed step while leaving your interactive
terminal open. The same file defines `show_deployment`, which prints the
InferenceService, PVC, and predictor Pod status for the project in `PROJECT`.

The example values below come from one NVIDIA L4 with the default model. Your
numbers will differ with the GPU, runtime build, and other workloads; compare the
**relative** changes rather than the absolute values. Client timing also includes
the local port-forward; keep the connection path consistent across comparisons.

### 1. `single`: one request as a baseline

```json
{"ok":"1/1","tokens":32,"latency_s":1.28,"tok_per_s":25}
```

| Field | Meaning |
| --- | --- |
| `summary.successful_requests` / `failed_requests` | The request succeeded: HTTP 200, a non-empty completion, and token usage reported. |
| `summary.output_tokens` | Tokens actually generated. With `IGNORE_EOS=1`, the model always produces exactly `MAX_TOKENS`, so the text may stop mid-sentence. |
| `summary.latency_seconds.mean` | How long one user waits for the complete answer. |
| `summary.output_tokens_per_second` | Generation speed when the GPU serves a single request. |

### 2. `benchmark`: many users sharing one GPU

```json
{"c":1,"ok":"12/12","tok_per_s":24.3,"e2e_s":1.32,"ttft_s":0.53,"tpot_ms":26}
{"c":4,"ok":"12/12","tok_per_s":88.3,"e2e_s":1.4,"ttft_s":0.58,"tpot_ms":26}
{"c":8,"ok":"12/12","tok_per_s":127.8,"e2e_s":1.46,"ttft_s":0.64,"tpot_ms":26}
```

| Field | Meaning |
| --- | --- |
| `configuration.concurrency` | How many requests are in flight at the same time. |
| `summary.successful_requests` | Check this first: throughput only counts successful requests. |
| `summary.output_tokens_per_second` | Total tokens per second across all requests: the server's aggregate throughput. |
| `summary.latency_seconds.mean` | Average time each request takes from start to finish (end-to-end, or E2E). |
| `summary.ttft_seconds.mean` | Time to first token: how long until text starts arriving (requires `STREAM=1`). |
| `summary.tpot_seconds.mean` | Time per output token for each request, after the first token. |

**What it shows:** in this example, going from 1 to 8 concurrent requests increased
aggregate throughput about 5x while mean request latency rose about 10%. The
reported per-token times stayed close: more requests were served at nearly the
same per-request generation time.

vLLM uses **continuous batching** to update the active batch between generation
steps. As requests finish and capacity becomes available, new work can join.
Compare your own throughput, latency, and TPOT values together when describing
the result.

Throughput and latency are a tradeoff. Raise concurrency until latency stops
meeting your application's needs, rather than picking the highest throughput.

### 3. `prefix`: reusing work for a repeated prompt

```json
{"reuse":true,"prompt_tokens":1960,"cached_tokens":1952,"ttft_cold_s":0.69,"ttft_warm_s":0.51,"tpot_cold_ms":26,"tpot_warm_ms":26}
```

The experiment sends the same long prompt twice: a `cold` request, then a `warm` one.

| Field | Meaning |
| --- | --- |
| `cache_reuse_observed` | `true` when both requests succeeded and the server's prefix-cache counters increased. |
| `warm.prompt_tokens` | The prompt length, like a long system prompt or a shared document. |
| `prefix_counter_delta.hits` | Increase in the server-wide cached prompt-token count during the experiment. Here, 1952 cache hits were observed with a 1960-token prompt. |
| `cold.ttft_seconds` vs `warm.ttft_seconds` | Compare when text first arrived in each request. Prefix reuse reduces prefill work; the observed warm TTFT can still vary with scheduling and transport. |
| `cold.tpot_seconds` vs `warm.tpot_seconds` | Compare the observed per-token generation times. Prefix caching reuses prompt state; decoding still generates the new answer tokens. |

**What it shows:** **prefix caching** reuses the attention state of a prompt prefix
that the server has already processed. It helps most when many requests share a
long beginning, such as system prompts, few-shot examples, or retrieved documents.
Run the experiment without other traffic to the predictor because the counters
are shared. A positive cache-hit delta confirms observed reuse during the pair;
check the timing values before describing a latency improvement.

## Files and manual workflow

- `00-run-lab.sh`: orchestration (`up` or `verify`).
- `01-setup-project.sh`: project, PVC, and model download.
- `02-deploy-runtime.sh`: runtime image resolution and deployment.
- `02-serving-runtime.yaml`: runtime image, server arguments, environment, health probes.
- `03-inference-service.yaml`: the model, referencing that runtime and `pvc://model-pvc/`.
- `03-deploy-model.sh`: applies the model with the placement saved on the PVC.
- `placement.py`: validates overrides and selects a compatible GPU node.
- `04-run-inference.sh`: waits for readiness, opens a port-forward, runs the client.
- `inference_client.py`: response validation, streaming timing, JSON results.
- `demo-helpers.sh`: interactive helpers to `source`: output summaries and `show_deployment`.
- `05-cleanup.sh`: deletes the project after confirmation.
- `tests/`: local tests; no cluster needed.

To run the steps individually:

```bash
./01-setup-project.sh vllm-demo
oc project vllm-demo
./02-deploy-runtime.sh vllm-demo
./03-deploy-model.sh vllm-demo
oc wait --for=condition=Ready inferenceservice/qwen25-3b --timeout=1200s
oc logs -l serving.kserve.io/inferenceservice=qwen25-3b -c kserve-container
PROJECT=vllm-demo ./04-run-inference.sh
```

`02-deploy-runtime.sh` takes the `kserve-container` image from the installed
OpenShift AI template and leaves `02-serving-runtime.yaml` unchanged. The digest
in that YAML is only an example. If the installed image can't be found, deployment
stops instead of falling back to the example. To use a specific image, set
`VLLM_IMAGE=registry.example/runtime:<version> ./02-deploy-runtime.sh vllm-demo`.

The InferenceService uses `predictor.model.runtime: vllm-cuda-runtime`. KServe
mounts the PVC in the model container, and readiness is based on the `/health`
endpoint. The client connects through a short-lived local port-forward to a Ready
predictor Pod, so requests don't go through an external route.

## Measurement and a practical demo

1. Show the runtime reference, GPU allocation, and a successful completion.
2. Compare concurrency 1, 4, 8, and 16 on the **same** model, runtime, GPU, and
   workload. Look at throughput and latency together.
3. Run the long-prefix experiment and compare cold and warm time to first token
   (TTFT) and the cache-hit counters.
4. Explain which capabilities come from the platform and which engine
   optimizations you actually observed.

Example sweep (use a fresh output directory; three repetitions per point):

```bash
mkdir benchmark-results
for repeat in 1 2 3; do
  for concurrency in 1 4 8 16; do
    STREAM=1 IGNORE_EOS=1 MAX_TOKENS=128 \
      RESULTS_FILE="benchmark-results/c${concurrency}-r${repeat}.json" \
      ./04-run-inference.sh benchmark "$concurrency" 32
  done
done
```

The client sends two warmup requests by default and excludes them from the
results. `MAX_TOKENS` is normally a ceiling; `IGNORE_EOS=1` is a vLLM extension
that forces fixed-length output for synthetic tests. The default prompt is short
and repeated, so it can benefit from prefix caching. It isn't a representative
production dataset.

What the numbers mean:

- Requests/s and output tokens/s count successful requests only, over the full
  measured wall time.
- Non-streaming latency covers the full response; it is **not TTFT**.
- `STREAM=1` reports client-observed time to the first text chunk. Time per output
  token (TPOT) is an estimate based on first-to-last text time and the generated
  token count.
- p50/p95/p99 use nearest-rank percentiles. Small samples don't support reliable
  tail-latency or SLO conclusions.
- HTTP errors, malformed responses, missing usage data, incomplete streams, and
  transport failures count as failures.
- Prometheus `*_tokens_total` metrics are counters; use `rate()` over an interval
  for tokens/s.

`prefix` sends two identical streaming requests with a long, unique prefix and
reads the `vllm:prefix_cache_queries_total` and `vllm:prefix_cache_hits_total`
[vLLM metrics](https://docs.vllm.ai/en/stable/usage/metrics/) before and after.
It reports `cache_reuse_observed: true` and exits 0 only when both requests
succeed and both counters increase. Because metrics publish asynchronously, the
client polls for up to `PREFIX_METRICS_TIMEOUT` seconds (default 10, maximum 60).
The output includes `cold`, `warm`, `prefix_counter_delta`, `cache_reuse_observed`,
and a `prefix_metrics` object with status details.

The counters are server-wide, so avoid other traffic to the predictor during the
experiment. Two requests show cache reuse; they don't measure a statistically
significant speedup. Prefix caching reuses full token blocks, so a very short
prompt may have nothing to reuse.

For sustained load and quality evaluation, use
[GuideLLM](https://github.com/vllm-project/guidellm) and
[lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness).
To compare engines, match the hardware, precision, model revision, prompt and
output lengths, cache state, concurrency, and transport, and repeat each
measurement.

## Configuration

- Setup: positional project name; `MODEL_ID`, `MODEL_REVISION`, `PVC_SIZE`
  (default 10Gi), `HF_TOKEN` for gated models, `STORAGE_CLASS`, `STORAGE_TOPOLOGY`
  (`auto` or `shared`), `STORAGE_ZONE`, `GPU_NODE_SELECTOR`, `GPU_TOLERATIONS`,
  `DOWNLOAD_TIMEOUT` (default 1800s), and `DOWNLOAD_IMAGE` to pin the downloader
  image.
- Deployment: `VLLM_IMAGE` overrides the image from the installed template, in both
  `02-deploy-runtime.sh` and `00-run-lab.sh`.
- Client: `PROJECT`, `MODEL_NAME`, `INGRESS_PORT` (8080), `READY_TIMEOUT`,
  `BASE_URL`, `API_KEY`, `REQUEST_TIMEOUT` (120 seconds), `PROMPT`, `MAX_TOKENS`,
  `STREAM`, `IGNORE_EOS`, `WARMUP_REQUESTS`, `RESULTS_FILE`.

`BASE_URL` targets an existing endpoint directly, skipping cluster access and the
port-forward. Set it to the final URL, since redirects are rejected. TLS
verification stays on, and `API_KEY` supplies a bearer token; use HTTPS for remote
endpoints. `BASE_URL` and `RESULTS_FILE` apply to individual client runs and are
rejected by `00-run-lab.sh`. Don't expose unauthenticated inference or metrics
publicly.

Setup records the model ID and revision on the PVC and won't reuse a download that
doesn't match. Use a new project to switch models. Rerunning while a download is
in progress waits for it, and a failed download Job is kept for diagnosis. To adapt
the lab to another model, change the model alias and resources in the manifests.

### Precision and multiple GPUs

`--dtype=auto` follows the model configuration; it doesn't quantize or guarantee
compatibility with every GPU. Use `--dtype=half` on a T4 for this BF16 model.
`--enforce-eager` disables CUDA graphs; removing it changes the benchmark
configuration.

For tensor parallelism, append `--tensor-parallel-size=2` to `predictor.model.args`
and change **both** GPU resource values:

```yaml
resources:
  requests:
    nvidia.com/gpu: "2"
  limits:
    nvidia.com/gpu: "2"
```

Keep the CPU and memory settings, and make sure matching GPUs are available. A
single-GPU node may not have room for a surge Pod during an update, so plan
redeployments. Serverless, KEDA, distributed serving, and canary rollouts are
outside the scope of this lab.

## Troubleshooting and cleanup

```bash
oc get pods,pvc,jobs -n vllm-demo
oc get events -n vllm-demo --sort-by=.lastTimestamp
oc logs job/download-model -n vllm-demo --tail=30
oc logs -n vllm-demo -l serving.kserve.io/inferenceservice=qwen25-3b \
  -c kserve-container --tail=50
./05-cleanup.sh vllm-demo
```

For Pending Pods, check GPU availability, taints, and PVC topology. For image-pull
failures, check registry credentials. For download failures, check the model
license, token, and network access. A download timeout doesn't delete the Job;
increase `DOWNLOAD_TIMEOUT` and rerun after checking its status. Cleanup asks for
confirmation and deletes the project, including model storage according to its
reclaim policy.

Run the local tests with `python3 -m unittest discover -s tests -v`. They also need
PyYAML (`python3 -m pip install PyYAML`, ideally in a virtual environment). The
tests use local fixtures and don't need cluster access.

## References

- [vLLM serving arguments (0.24.0)](https://docs.vllm.ai/en/v0.24.0/cli/serve/)
- [vLLM prefix-caching design (0.24.0)](https://docs.vllm.ai/en/v0.24.0/design/prefix_caching/)
- [OpenShift AI documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed)
- [KServe documentation](https://kserve.github.io/website/)
- [Kubernetes GPU scheduling](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)

Upstream vLLM and KServe documentation describes engine behavior. For supported
combinations, use the OpenShift AI documentation for your installed release.
