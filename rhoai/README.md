# vLLM on Red Hat OpenShift AI

A reproducible GPU inference lab using KServe, a PVC-backed model, and the
OpenAI-compatible vLLM API. This is an educational lab, not a production security
or capacity-planning reference. The scripts do not configure public routes,
authentication policies, autoscaling, or monitoring dashboards.

## Validated configuration

The full disposable lifecycle passed on October 24, 2026, with this configuration:

| Component | Tested version or setting |
| --- | --- |
| OpenShift Container Platform | 4.22.19 |
| Red Hat OpenShift AI | 3.5.2 |
| GPU Operator / NVIDIA driver | 26.7.2 / 615.71.09 |
| GPU | One NVIDIA L4, 24 GB class |
| Runtime package | vLLM 0.24.0+rhaiv.15 |
| Model | Qwen2.5-3B-Instruct, pinned revision below |
| Serving | KServe RawDeployment, BF16, eager execution, 4096-token context |
| Prefix caching | Enabled |

Validation covered fresh model download, runtime deployment, readiness, single
inference, streaming concurrency at 1/4/8, metrics, prefix-cache counters, and
automatic UID-protected cleanup. A separate Pod-local check validated the model
listing and chat-completions API. The predictor stayed on the same image and node
with zero restarts during measurement. External inference ingress/authentication,
autoscaling, multi-GPU operation, and production capacity were not tested.

These are recorded test versions, not moving `latest` requirements. Resolve the
runtime from your installed template and consult the
[OpenShift AI supported configurations](https://access.redhat.com/articles/rhoai-supported-configs)
and [AI Inference version matrix](https://access.redhat.com/articles/rhaii-supported-configs)
when choosing your own compatible stack. Use a prepared cluster that meets the
prerequisites below.

### Portability validation

On October 26, 2026, the disposable lifecycle also passed on OpenShift 4.22.16,
OpenShift AI 3.5.1, and vLLM 0.24.0+rhaiv.13 with one NVIDIA L4. That cluster had
mixed GPU generations, a custom GPU scheduling taint, and no node zone labels.
The resolver selected a compatible GPU without a hostname in the source files.
Separate fresh runs exercised shared Ceph RBD storage with `Immediate` binding
and Ceph RBD with `WaitForFirstConsumer`, including model download, serving,
concurrency 1/4/8, metrics, observed cache reuse, and project cleanup.

These results validate those configurations. Other accelerator types, model
sizes, storage drivers, network policies, and quotas need their own lifecycle
check. The inference protocol and the lab's purpose remain the same across
clusters: observe serving, concurrency, and prefix-cache behavior on a measured
configuration.

## Prerequisites

- OpenShift AI 3.5 with KServe managed and an available NVIDIA GPU supported by
  the installed CUDA runtime. The resolver requires GPU Feature Discovery labels
  and compute capability 7.5 or newer; model memory and runtime-specific kernel
  requirements still apply. The default model was tested on a 24 GB class L4.
- A storage class that can provide the requested PVC. The default class is used;
  set `STORAGE_CLASS` to select another. `WaitForFirstConsumer` is the safe default
  for storage with node or zone restrictions. For shared storage with `Immediate`
  binding, set `STORAGE_TOPOLOGY=shared` only after confirming that its volumes
  can be mounted from the selected GPU node. This is a storage accessibility
  declaration, not an automatic check of the backend.
- An authenticated `oc` client matching your OpenShift release, Bash, Python 3.9+,
  and `curl`. Obtain the client from your console's **Command Line Tools** page
  or the [OpenShift CLI download instructions](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/cli_tools/openshift-cli-oc).
  Python uses only its
  standard library. Read access to GPU nodes, Pods across namespaces (for GPU
  allocation checks), storage classes, and the runtime template is needed,
  in addition to permissions to create projects and namespaced serving resources.
- Registry access for the selected runtime, and cluster egress to the model hub
  and Python package index. The download image is UBI Python 3.11 and installs
  `huggingface_hub==0.34.4`.

This configuration uses KServe RawDeployment on OpenShift AI 3.5, not Knative.
Follow the prerequisites for your installed OpenShift AI release rather than
assuming all serving modes require the same operators.

### Model license

The default is `Qwen/Qwen2.5-3B-Instruct`, pinned to commit
`aa8e72537993ba99e69dfaafa59ed015b17504d1`. Its
[Qwen Research License](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE)
grants research/evaluation use and directs commercial users to request a separate
license. The repository's software license does not relicense model weights.
Review the selected model's terms before commercial deployment.

## Quick start

Run from this directory after logging in to your own cluster:

```bash
./00-run-lab.sh up vllm-demo
export PROJECT=vllm-demo
./04-run-inference.sh single
STREAM=1 RESULTS_FILE=benchmark-c4.json ./04-run-inference.sh benchmark 4 32
./04-run-inference.sh metrics
MAX_TOKENS=64 ./04-run-inference.sh prefix
```

`up` downloads the model, resolves the runtime image from the installed RHOAI
template, deploys, waits for model health, and runs one validated inference. It
leaves the lab running. First-time download and image pulls can take many minutes;
prepare the lab before a live demonstration.

### Placement on different clusters

The scripts discover the installed runtime image and available compatible GPUs.
They exclude unsupported GPU architectures, cordoned or unready nodes, nodes
with unmatched scheduling taints, and GPUs already allocated to other Pods.
The selection is a point-in-time check, not a reservation. CPU, host memory,
GPU memory, quotas, and concurrent scheduling can still prevent deployment.

Set only the overrides your cluster requires before running either workflow:

```bash
# Optional: limit selection to a particular GPU pool using Kubernetes labels.
export GPU_NODE_SELECTOR='{"nvidia.com/gpu.product":"NVIDIA-L4"}'

# Optional: replace the default nvidia.com/gpu toleration with the exact
# tolerations allowed for your GPU pool. Do not bypass another team's taints.
export GPU_TOLERATIONS='[{"key":"nvidia.com/gpu","operator":"Exists","effect":"NoSchedule"},{"key":"feature.node.kubernetes.io/gpu-node","operator":"Exists","effect":"NoSchedule"}]'

# Optional: choose a non-default storage class.
export STORAGE_CLASS=your-storage-class

# Required only for Immediate binding on confirmed shared storage.
export STORAGE_TOPOLOGY=shared
```

Do not copy every override blindly. Unset optional variables to use discovery.
The default toleration is `nvidia.com/gpu` with `Exists` and `NoSchedule`.
`GPU_NODE_SELECTOR` is a JSON map of exact label matches; `GPU_TOLERATIONS` is a
JSON array with explicit keys and effects. Neither contains a required GPU model,
node name, storage class, or cluster endpoint. `STORAGE_ZONE` remains available
as an optional filter for clusters that have `topology.kubernetes.io/zone` labels.

The chosen hostname and tolerations are recorded on the model PVC. The download
Job and predictor use that same placement, including on clusters without zone
labels. A rerun reuses the saved placement; use a new project to move the lab to
another node or GPU pool. An older PVC with topology constraints but no saved
placement requires a fresh project rather than guessing where its volume fits.
The download Job uses CPU and memory; it does not request a GPU.

After changing clusters, run the full lifecycle check below before using the lab
live. A different runtime build, GPU, or shared workload can change measured
throughput and latency. Keep configuration and benchmark evidence together.

`RESULTS_FILE` is optional and must not already exist. JSON output includes
per-request status, actual token counts, errors, and timing. Keep raw evidence
outside version control unless it has been reviewed for sensitive prompts and
generated content. Credentials and endpoint URLs are not included in successful
result records.

### Fully automated lifecycle check

```bash
./00-run-lab.sh verify vllm-lifecycle-check
```

`verify` exclusively creates a new project and refuses an existing one. It runs setup, deployment, readiness, single
inference, concurrent benchmarks at 1, 4, and 8 requests in flight, metrics, and a prefix-cache experiment, then
deletes only that verification project, using its UID as a server-side deletion
precondition. A recreated project with the same name is not deleted. A failing step exits nonzero and leaves
resources for inspection. A printed lifecycle pass means all commands succeeded;
it is not a performance superiority or production-readiness certification.
By default, `verify` uses streaming, 32 completion tokens, EOS ignored, and 12
measured requests at each concurrency level. `MAX_TOKENS` and `IGNORE_EOS` can
override those two settings. The inference client excludes warm-up requests from
its benchmark totals. Keep the same protocol when comparing runs.

## Files and manual workflow

- `00-run-lab.sh`: orchestration (`up` or disposable `verify`).
- `01-setup-project.sh`: project, annotated PVC, immutable model download.
- `02-deploy-runtime.sh`: shared image resolution and runtime deployment.
- `02-serving-runtime.yaml`: runtime image, API process, environment, health probes.
- `03-inference-service.yaml`: references that runtime and `pvc://model-pvc/`.
- `03-deploy-model.sh`: applies the model using the PVC's saved placement.
- `placement.py`: validates configuration and selects a compatible GPU node.
- `04-run-inference.sh`: readiness and owned port-forward, then the Python client.
- `inference_client.py`: response validation, streaming timing, JSON evidence.
- `05-cleanup.sh`: confirmed project deletion, with errors propagated.
- `tests/`: HTTP fixtures and shell regressions; no cluster needed.

For individual steps:

```bash
./01-setup-project.sh vllm-demo
oc project vllm-demo
./02-deploy-runtime.sh vllm-demo
./03-deploy-model.sh vllm-demo
oc wait --for=condition=Ready inferenceservice/qwen25-3b --timeout=1200s
oc logs -l serving.kserve.io/inferenceservice=qwen25-3b -c kserve-container
PROJECT=vllm-demo ./04-run-inference.sh
```

Both workflows use `02-deploy-runtime.sh` to select the `kserve-container` image
from the installed RHOAI template. The helper preserves the local runtime
configuration and never edits `02-serving-runtime.yaml`. The YAML's pinned
digest is an OpenShift AI 3.5.2 example, not a mechanism for selecting the latest
compatible build. The documented workflows resolve the image from the installed
template. A missing or ambiguous installed image stops deployment instead of
silently using that example. To select a compatible image explicitly, use
`VLLM_IMAGE=registry.example/runtime:<tested-version> ./02-deploy-runtime.sh vllm-demo`.

The helper also copies the optional `opendatahub.io/runtime-version` display
annotation from the same selected ServingRuntime template object. An explicit
`VLLM_IMAGE` override bypasses template lookup and does not infer a package version;
no runtime-version annotation is added in that case. This display metadata does
not prove the vLLM Python package version installed in the running container.

The model specification uses `predictor.model.runtime: vllm-cuda-runtime`; it is
not a separate custom-container predictor. KServe mounts the PVC in the model
container. Readiness is based on `/health`, not merely the Pod's `Running` phase.
The client connects through a short-lived local port-forward to the predictor
Pod selected by its Ready condition. This bypasses an external ingress route and
is not a route/auth test.

## Measurement and a practical demo

1. Show the runtime reference, GPU allocation, and a successful completion.
2. Compare concurrency 1, 4, 8, and 16 on the **same** model, runtime, GPU, and
   workload. Record both throughput and latency rather than choosing one winner.
3. Run the long-prefix experiment; compare cold/warm TTFT and cache-hit deltas.
4. Explain which operational capabilities are provided by the platform, separately
   from which engine optimizations were actually observed.

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

The client performs two excluded warmup requests by default. `MAX_TOKENS` is
normally a ceiling; `IGNORE_EOS=1` is a vLLM extension for fixed-length synthetic
tests. The repeated default prompt is intentionally simple and can benefit from
prefix caching. It is not a representative production dataset. Use varied,
versioned prompts and controlled cache policy when comparing engines.

Measurements:

- Successful requests/s and actual output tokens/s use the full measured wall time.
- Non-streaming latency includes the full response; it is **not TTFT**.
- `STREAM=1` reports client-observed time to first nonempty text chunk. TPOT is an
  estimate from first-to-last text time and the server's generated token count;
  network buffering and chunking affect it. It is not a per-token ITL trace.
- p50/p95/p99 use nearest-rank percentiles over successful requests. Small samples
  do not support reliable tail-latency or SLO conclusions.
- HTTP errors, malformed responses, missing usage, incomplete streams, and
  transport failures are failures, not successful throughput.
- Prometheus `*_tokens_total` values are counters. Use `rate()` over a suitable
  interval for tokens/s. An idle cache snapshot is not a utilization benchmark.

`prefix` uses a unique, long repeated prefix with two sequential streaming
requests. It observes `vllm:prefix_cache_queries_total` and
`vllm:prefix_cache_hits_total`: the local prefix-cache token counters documented
by [vLLM](https://docs.vllm.ai/en/stable/usage/metrics/), exposed with the
[`_total` suffix](https://prometheus.github.io/client_python/instrumenting/counter/).
External-cache counters and similarly named metrics are not substitutes.

Both counters must be present in the baseline; missing samples are not zero.
After the requests, the client polls every 0.25 seconds for asynchronous metric
publication, for up to `PREFIX_METRICS_TIMEOUT` seconds (default 10; must be
greater than 0 and at most 60). Each polling request uses the smaller of
`REQUEST_TIMEOUT` and the remaining polling budget as its socket timeout.
The polling budget starts after inference; it does not bound the baseline fetch
or the two inference requests, which use `REQUEST_TIMEOUT` separately.

Exit status 0 and `cache_reuse_observed: true` require two successful requests
and positive deltas for **both** hit and query counters within that window.
Missing counters, no observed reuse, malformed/nonfinite/negative values,
duplicate samples, counter decreases/resets, or changed series cannot pass.
Totals or deltas with more hits than queries are rejected as inconsistent evidence.
An observed zero remains numeric zero; unavailable or invalid deltas are `null`.
The existing `cold`, `warm`, `prefix_counter_delta`, `cache_reuse_observed`, and
`interpretation` evidence fields remain. The additive `prefix_metrics` object
records baseline/latest counts, poll count, timeout, elapsed polling time, and
status (`observed`, `not_observed`, `unavailable`, `invalid`, or `request_failed`),
with an error explanation when validation fails.

Counters are aggregated across the server's exposed series and are shared; avoid
other traffic. Positive deltas do not establish that these two requests caused
the reuse, and two timings do not guarantee a causal or statistically significant
speedup. Caching reuses full token blocks and can work with the completions API;
a very short prompt may have no reusable block.

For sustained workloads and quality assessment, use
[GuideLLM](https://github.com/vllm-project/guidellm) and
[lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness).
Compare engines on matched hardware, precision, model revision, tokenizer,
prompt/output lengths, cache state, concurrency, and transport. Retain errors,
warmup policy, repeated measurements, software versions, and raw data. Never
attribute a GPU-versus-CPU configuration difference solely to the engine.

## Configuration

- Setup: positional project name; `MODEL_ID`, immutable `MODEL_REVISION`,
  `PVC_SIZE` (default 10Gi), `HF_TOKEN` for gated models, `STORAGE_CLASS`,
  `STORAGE_TOPOLOGY` (`auto` or explicit `shared`), `STORAGE_ZONE`,
  `GPU_NODE_SELECTOR`, `GPU_TOLERATIONS`, and
  `DOWNLOAD_TIMEOUT` (default 1800s).
  `DOWNLOAD_IMAGE` can pin a compatible UBI Python 3.11 image digest; the default
  image tag follows UBI updates. Record resolved image digests with your evidence.
- Deployment: `VLLM_IMAGE` overrides installed-template image resolution in both
  `02-deploy-runtime.sh` and `00-run-lab.sh`.
  Check model/runtime/hardware support for the specific downstream release.
- Client: `PROJECT`, `MODEL_NAME`, `INGRESS_PORT` (8080), `READY_TIMEOUT`,
  `BASE_URL`, `API_KEY`, `REQUEST_TIMEOUT` (120 seconds), `PROMPT`, `MAX_TOKENS`,
  `STREAM`, `IGNORE_EOS`, `WARMUP_REQUESTS`, `RESULTS_FILE`.

`BASE_URL` skips cluster access and port-forwarding, allowing an existing secured
endpoint. TLS verification stays enabled; `API_KEY` supplies a bearer token.
The local port-forward is bound to loopback and only its own child process is
stopped. Do not expose unauthenticated inference or metrics publicly.
`BASE_URL` and `RESULTS_FILE` are rejected by `00-run-lab.sh`; they belong to
individual client invocations, not the self-contained lifecycle check.

Set `BASE_URL` to the final endpoint: completions and metrics reject HTTP redirects,
including same-origin redirects, before sending any request to the destination.
Direct loopback HTTP and direct HTTPS remain supported. Use HTTPS for remote
endpoints carrying `API_KEY`; plain HTTP does not encrypt the bearer token.

Setup records model identity on the PVC and model/revision/PVC UID on the download
Job. A missing PVC, mismatched Job, or unrecorded identity cannot be treated as
a completed download. Use a new project to change models without mixing files. Rerunning
while a download is active waits for it; it does not delete the Job. A failed Job
is preserved for diagnosis. Change the model alias and resource settings in the
manifests when adapting the lab to another model.

### Precision and multiple GPUs

`--dtype=auto` follows the model configuration; it does not automatically quantize
to FP8 or guarantee compatibility with every GPU. Use `--dtype=half` on T4 for
this BF16 model. `--enforce-eager` disables CUDA-graph execution; it is not a
general fix for every Triton or filesystem issue. Writable cache directories are
configured separately. Removing eager mode changes the benchmark configuration.

For tensor parallelism, append `--tensor-parallel-size=2` to `predictor.model.args`
and change **both** GPU resource values:

```yaml
resources:
  requests:
    nvidia.com/gpu: "2"
  limits:
    nvidia.com/gpu: "2"
```

Preserve the CPU/memory settings. Matching GPUs and topology must be available.
A single-GPU node may not have capacity for surge Pods during an update; plan an
explicit maintenance/redeployment strategy. Serverless, KEDA, distributed serving,
and canary rollout need release-specific configuration and are outside this lab.

## Troubleshooting and cleanup

```bash
oc get pods,pvc,jobs -n vllm-demo
oc get events -n vllm-demo --sort-by=.lastTimestamp
oc logs job/download-model -n vllm-demo --tail=30
oc logs -n vllm-demo -l serving.kserve.io/inferenceservice=qwen25-3b \
  -c kserve-container --tail=50
./05-cleanup.sh vllm-demo
```

Check GPU availability and PVC topology for Pending Pods; registry credentials
for image-pull failures; model license/token and network access for download
failures. A download timeout does not delete its Job. Increase `DOWNLOAD_TIMEOUT`
and rerun after inspecting its status. Cleanup asks for confirmation and deletes
the selected project, including model storage according to its reclaim policy.

Run local tests with `python3 -m unittest discover -s tests -v`. The tests additionally
require PyYAML for semantic manifest checks; install it in a virtual environment
with `python3 -m pip install PyYAML`. The runtime scripts and client still use only
the Python standard library. Tests use loopback HTTP and controlled command
fixtures, not cluster credentials. A clean local test run does not replace the
disposable lifecycle check.

## References

Upstream references explain engine behavior; they do not define Red Hat support.
Use the OpenShift AI documentation for the prerequisites and supported combinations
of your installed product release.

- [Upstream vLLM serving arguments, version 0.24.0](https://docs.vllm.ai/en/v0.24.0/cli/serve/)
- [Upstream vLLM prefix-cache design, version 0.24.0](https://docs.vllm.ai/en/v0.24.0/design/prefix_caching/)
- [OpenShift AI documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed)
- [KServe documentation](https://kserve.github.io/website/)
- [Kubernetes GPU resource requirements](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)
