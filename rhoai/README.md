# vLLM on Red Hat OpenShift AI

A reproducible GPU inference lab using KServe, a PVC-backed model, and the
OpenAI-compatible vLLM API. This is an educational lab, not a production security
or capacity-planning reference. The scripts do not configure public routes,
authentication policies, autoscaling, or monitoring dashboards.

## Prerequisites

- OpenShift AI with KServe managed and an NVIDIA GPU with capacity available.
- A storage class with `WaitForFirstConsumer` binding. The script selects the
  default class; set `STORAGE_CLASS` explicitly if needed. Immediate-binding
  classes are rejected because they can provision a volume outside the GPU zone.
  For zonal storage, the download must run in a zone
  containing a usable GPU. The setup selects the first labeled GPU node's zone;
  set `STORAGE_ZONE` explicitly when multiple GPU pools exist. On clusters without
  zone labels, it uses that GPU node's hostname for the download Job.
- An authenticated `oc` client, Bash, Python 3.9+, and `curl`. Python uses only its
  standard library. Read access to node labels and the runtime template is needed,
  in addition to permissions to create projects and namespaced serving resources.
- Registry access for the selected runtime, and cluster egress to the model hub
  and Python package index. The download image is UBI Python 3.11 and installs
  `huggingface_hub==0.34.4`.

This configuration uses RawDeployment, not Knative. Follow the prerequisites for
your installed OpenShift AI release rather than assuming all serving modes require
the same operators. The supplied configuration targets the 2.25 serving APIs;
features documented for 3.5 are not automatically available here.

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
inference, a concurrent benchmark, metrics, and a prefix-cache experiment, then
deletes only that verification project, using its UID as a server-side deletion
precondition. A recreated project with the same name is not deleted. A failing step exits nonzero and leaves
resources for inspection. A printed lifecycle pass means all commands succeeded;
it is not a performance superiority or production-readiness certification.

## Files and manual workflow

- `00-run-lab.sh`: orchestration (`up` or disposable `verify`).
- `01-setup-project.sh`: project, annotated PVC, immutable model download.
- `02-serving-runtime.yaml`: runtime image, API process, environment, health probes.
- `03-inference-service.yaml`: references that runtime and `pvc://model-pvc/`.
- `04-run-inference.sh`: readiness and owned port-forward, then the Python client.
- `inference_client.py`: response validation, streaming timing, JSON evidence.
- `05-cleanup.sh`: confirmed project deletion, with errors propagated.
- `tests/`: HTTP fixtures and shell regressions; no cluster needed.

For individual steps:

```bash
./01-setup-project.sh vllm-demo
oc project vllm-demo
# Inspect the compatible image for your installed release:
oc get template vllm-cuda-runtime-template -n redhat-ods-applications \
  -o jsonpath='{.objects[0].spec.containers[0].image}'
# Update the image in 02-serving-runtime.yaml if necessary.
oc apply -f 02-serving-runtime.yaml
oc apply -f 03-inference-service.yaml
oc wait --for=condition=Ready inferenceservice/qwen25-3b --timeout=1200s
oc logs -l serving.kserve.io/inferenceservice=qwen25-3b -c kserve-container
PROJECT=vllm-demo ./04-run-inference.sh
```

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
requests. It reports `cache_reuse_observed` and server counter deltas. The counters
are shared; avoid other traffic. Two timings illustrate behavior but do not prove
a statistically significant speedup. Caching reuses full token blocks and can
work with the completions API; a very short prompt may have no reusable block.

For sustained workloads and quality assessment, use
[GuideLLM](https://github.com/vllm-project/guidellm) and
[lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness).
Compare engines on matched hardware, precision, model revision, tokenizer,
prompt/output lengths, cache state, concurrency, and transport. Retain errors,
warmup policy, repeated measurements, software versions, and raw data. Never
attribute a GPU-versus-CPU configuration difference solely to the engine.

## Configuration

- Setup: positional project name; `MODEL_ID`, immutable `MODEL_REVISION`,
  `PVC_SIZE` (default 10Gi), `HF_TOKEN` for gated models, `STORAGE_CLASS`, `STORAGE_ZONE`, and
  `DOWNLOAD_TIMEOUT` (default 1800s).
  `DOWNLOAD_IMAGE` can pin a compatible UBI Python 3.11 image digest; the default
  image tag follows UBI updates. Record resolved image digests with your evidence.
- Deployment: `VLLM_IMAGE` overrides installed-template image resolution in `up`.
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

Run local tests with `python3 -m unittest discover -s tests -v`. They use loopback
HTTP and controlled command fixtures, not cluster credentials. A clean local test
run does not replace the disposable lifecycle check.

## References

- [vLLM serving arguments, version 0.10.1](https://docs.vllm.ai/en/v0.10.1/cli/serve.html)
- [vLLM prefix-cache design](https://docs.vllm.ai/en/v0.10.1/design/prefix_caching.html)
- [OpenShift AI documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed)
- [KServe documentation](https://kserve.github.io/website/)
- [Kubernetes GPU resource requirements](https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/)
