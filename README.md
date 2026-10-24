# vLLM + KServe Demo

This project demonstrates deploying and testing LLM inference using **KServe** with **vLLM** as the runtime. Benchmarked models include **[Gemma-3-270m](https://huggingface.co/google/gemma-3-270m)** (270M params) and **[Qwen2.5-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)** (3.09B params). Three deployment examples are provided:

- **[Kind](kind/)** - Lightweight Kubernetes cluster using Podman Desktop
- **[MicroShift](microshift/)** - Single-node OpenShift (via [minc](https://github.com/minc-org/minc)) for an OpenShift-compatible experience
- **[Red Hat OpenShift AI (RHOAI)](rhoai/)** - GPU-serving lab; check runtime, model and hardware compatibility for your installed release

---

## Repository Structure

```
vllm-local-demo/
├── kind/
│   ├── 01-install_kserve.sh        # KServe install (curl | bash)
│   ├── 02-inference-service.yaml   # InferenceService manifest
│   └── 03-run-inference.sh         # Inference + benchmark script (kubectl)
├── microshift/
│   ├── 01-install_kserve.sh        # KServe install (step-by-step with SCC grants)
│   ├── 02-inference-service.yaml   # InferenceService manifest
│   └── 03-run-inference.sh         # Inference + benchmark script (oc)
├── rhoai/
│   ├── 00-run-lab.sh               # Prepare a lab or verify its full lifecycle
│   ├── 01-setup-project.sh         # Create project, PVC, download model
│   ├── 02-deploy-runtime.sh        # Resolve installed image and deploy runtime
│   ├── 02-serving-runtime.yaml     # vLLM CUDA ServingRuntime for KServe
│   ├── 03-inference-service.yaml   # InferenceService with GPU (RawDeployment)
│   ├── 04-run-inference.sh         # Validated inference, benchmark, metrics, prefix
│   ├── inference_client.py        # Token counts, timing and per-request evidence
│   ├── 05-cleanup.sh               # Remove all resources
│   ├── tests/                     # Isolated HTTP and command regression tests
│   └── README.md                   # RHOAI-specific documentation
├── llamacpp-bench.sh               # llama.cpp server benchmark script
└── README.md
```

---

## Prerequisites

1. **Model access and license** - Review the selected model's terms. Gated or private models require a Hugging Face token: [Hugging Face tokens guide](https://huggingface.co/docs/hub/en/security-tokens). Follow the prerequisites for the chosen workflow; a token is not a universal requirement for every model.

2. **A compatible cluster** - Choose Kind, MicroShift, or the [OpenShift AI GPU lab](rhoai/README.md). Each workflow has its own infrastructure and client prerequisites.

---

## Option A: Kind + Podman Desktop

### Platform-Specific Setup

#### macOS

   1. **Install Podman Desktop**:

      ```bash
      brew install --cask podman-desktop
      ````

   2. **Enable a rootful Podman machine** (required for Kind):

      ```bash
      podman machine stop
      podman machine rm
      podman machine init --rootful
      podman machine start
      ```

   3. You may need to **set environment variable** (for non-UI use):

      ```bash
      export KIND_EXPERIMENTAL_PROVIDER=podman
      ```

   4. **Install Kind CLI** (if not auto-installed):

      ```bash
      brew install kind
      ```

   5. **Install kubectl**:

      ```bash
      brew install kubectl
      ```

   6. **Create Kind cluster via Podman Desktop GUI**:

      * Open Podman Desktop -> go to **Extensions** -> confirm **Kind extension** is present (built-in).
      * Navigate to **Settings > Resources**, locate the **Kind tile**, and click **Create new...** to start the cluster (either default or custom config).
      * Use Podman Desktop's Kubernetes UI or system tray to **watch logs** and **set the `kind-<name>` context**.

#### Windows

1. **Install Podman Desktop** (installer enables WSL/backing setup).

2. **Create a rootful Podman machine** if needed:

    ```powershell
    podman machine stop
    podman machine set --rootful
    podman machine start
    ```

3. **Install Kind CLI** (Chocolatey or manual):

    ```powershell
    choco install kind
    ```

4. **Install kubectl**:

    ```powershell
    choco install kubernetes-cli
    ```

5. **Create Kind cluster via Podman Desktop GUI**, as described under macOS.

#### CLI Fallback

> If you prefer CLI or automation:
>
> ```bash
> KIND_EXPERIMENTAL_PROVIDER=podman
> kind create cluster
> ```
>
> Kind auto-detects Podman as the runtime.

### Deploy on Kind

```bash
cd kind/

# Create HuggingFace secret
kubectl create secret generic huggingface-secret --from-literal=token=<your_token>

# Install KServe
./01-install_kserve.sh

# Deploy the model
kubectl apply -f 02-inference-service.yaml

# Wait for model to be ready (2/2)
kubectl get pods -w

# Run inference
./03-run-inference.sh

# Run benchmark
./03-run-inference.sh benchmark           # 5 concurrent, 10 total
./03-run-inference.sh benchmark 10 50     # 10 concurrent, 50 total
```

---

## Option B: MicroShift (via minc)

[MicroShift](https://microshift.io/) is a single-node OpenShift distribution that runs as a container using [minc](https://github.com/minc-org/minc). It provides an OpenShift-compatible environment with Security Context Constraints (SCCs), OpenShift Router, and OLM.

### Prerequisites (Linux only)

1. **Podman** installed (rootful or rootless)
2. **oc CLI** installed
3. **helm** installed
4. **VPN disconnected** - Corporate VPNs typically break container networking for rootful Podman. Disconnect before creating the cluster.

### Create the MicroShift Cluster

```bash
# Install minc
curl -L -o minc https://github.com/minc-org/minc/releases/latest/download/minc_linux_amd64
chmod +x minc
sudo mv minc /usr/local/bin/minc
```

#### Option 1: Rootful (via CLI)

```bash
# Configure DNS for rootful Podman (required if default DNS is corporate/internal)
sudo mkdir -p /etc/containers/containers.conf.d
echo -e '[containers]\ndns_servers = ["8.8.8.8", "8.8.4.4"]' | sudo tee /etc/containers/containers.conf.d/dns.conf

# Create the cluster
sudo minc create

# Extract kubeconfig
sudo podman cp microshift:/var/lib/microshift/resources/kubeadmin/kubeconfig ~/.kube/config-microshift
sudo chown $USER ~/.kube/config-microshift
```

#### Option 2: Rootless (via Podman Desktop)

```bash
# Create the cluster (no sudo needed)
minc create

# Extract kubeconfig
podman cp microshift:/var/lib/microshift/resources/kubeadmin/kubeconfig ~/.kube/config-microshift
```

#### Verify

```bash
export KUBECONFIG=~/.kube/config-microshift
oc get pods -A
```

### Deploy on MicroShift

```bash
cd microshift/

# Create HuggingFace secret
oc create secret generic huggingface-secret --from-literal=token=<your_token>

# Install KServe (handles SCC grants automatically)
./01-install_kserve.sh

# Deploy the model
oc apply -f 02-inference-service.yaml

# Wait for model to be ready (2/2)
oc get pods -w

# Run inference
./03-run-inference.sh

# Run benchmark
./03-run-inference.sh benchmark           # 5 concurrent, 10 total
./03-run-inference.sh benchmark 10 50     # 10 concurrent, 50 total
```

### Key Differences from Kind

| Aspect | Kind | MicroShift |
|--------|------|------------|
| CLI | `kubectl` | `oc` |
| Security | PodSecurity standards | OpenShift SCCs (requires `anyuid` for Istio) |
| KServe install | `curl \| bash` (self-contained) | Step-by-step with `--server-side` apply |
| Container runtime | Podman rootless | Podman rootful or rootless |
| VPN compatibility | Works with VPN | Requires VPN disconnected |
| Ingress | Built-in (via Podman Desktop) | OpenShift Router + Istio |

---

## Benchmarking

This project includes benchmark tools for both vLLM (via KServe) and llama.cpp, enabling a direct comparison between the two inference runtimes.

### vLLM Benchmark (KServe)

The inference scripts (`03-run-inference.sh`) include a built-in benchmark mode:

| Mode | Command | Description |
|------|---------|-------------|
| Single request | `./03-run-inference.sh` | One inference with detailed timing metrics |
| Benchmark | `./03-run-inference.sh benchmark` | 10 requests, 5 concurrent (default) |
| Custom benchmark | `./03-run-inference.sh benchmark [concurrency] [total]` | Custom concurrency and request count |

### llama.cpp Benchmark

The `llamacpp-bench.sh` script benchmarks any llama.cpp-compatible server (OpenAI API):

```bash
# Single request
./llamacpp-bench.sh

# Benchmark: 10 requests, 5 concurrent (default)
./llamacpp-bench.sh benchmark

# Custom: 10 concurrent, 50 total
./llamacpp-bench.sh benchmark 10 50
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLAMA_HOST` | `localhost` | Server hostname |
| `LLAMA_PORT` | `8080` | Server port |
| `LLAMA_BACKEND` | `auto` | Label override (e.g., `Vulkan`, `CPU`) |

The script auto-detects the model name and server slot count from the `/props` endpoint.

---

## Testing with llama.cpp (Baseline Comparison)

To compare vLLM against llama.cpp on the same model, you can use either [ramalama](https://github.com/containers/ramalama) (convenient CLI wrapper) or a pure llama.cpp build.

### Option 1: Using Ramalama

Ramalama wraps llama.cpp and handles model downloads automatically:

```bash
# Install ramalama
pip install ramalama

# Serve with Vulkan GPU acceleration (if available)
ramalama serve hf://AlexonOliveiraRH/gemma-3-270m-Q4_K_M-GGUF

# Serve CPU-only (disable GPU offloading)
ramalama serve --ngl 0 hf://AlexonOliveiraRH/gemma-3-270m-Q4_K_M-GGUF

# Serve CPU-only with limited threads (to match container resource constraints)
ramalama serve --ngl 0 --threads 4 hf://AlexonOliveiraRH/gemma-3-270m-Q4_K_M-GGUF

# Serve F16 precision (matches vLLM's default FP16)
ramalama serve --ngl 0 --threads 4 hf://unsloth/gemma-3-270m-it-GGUF:gemma-3-270m-it-F16.gguf
```

Key ramalama flags:
- `--ngl 0` - CPU-only (no GPU layer offloading)
- `--threads N` - limit CPU threads
- `--ctx-size N` - context window size

### Option 2: Using Pure llama.cpp

Building llama.cpp from source gives access to the latest optimizations:

```bash
# Clone and build
git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -DGGML_VULKAN=OFF -DGGML_CPU=ON
cmake --build build --config Release -j$(nproc)

# Download a GGUF model (e.g., from Hugging Face)
# Place the .gguf file in a known location

# Start the server
./build/bin/llama-server \
    -m /path/to/gemma-3-270m-it-F16.gguf \
    --host 0.0.0.0 --port 8080 \
    -t 4 \          # threads
    -np 4 \         # parallel slots (concurrent requests)
    -c 4096 \       # total context size
    --no-mmap
```

### Run the Benchmark

With either server running:

```bash
# Quick single-request test
./llamacpp-bench.sh

# Full benchmark (10 concurrent, 50 total)
./llamacpp-bench.sh benchmark 10 50
```

---

## Performance Comparison: vLLM vs llama.cpp

All tests used identical request parameters (prompt: *"Being an IT professional is"*, max 30 tokens, stop on `.`). Local tests ran on the same machine (CPU-only, no NVIDIA GPU). The RHOAI tests ran on an OpenShift cluster in AWS with a Tesla T4 GPU.

**Historical exploratory results, not a controlled engine comparison.** These
tables used different hardware, precisions, runtime versions, and serving paths.
The original timing-only harness did not retain HTTP status, response validity,
or actual output-token counts for each request. Treat the values as recorded
observations, not verified successful-throughput or causal performance evidence.
Use the failure-aware [`rhoai/`](rhoai/) workflow for new measurements.

### Gemma-3-270m Results (10 concurrent, 50 requests)

| # | Runtime | Precision | Hardware | Cores | Throughput | Wall Clock | Latency (min/max) |
|---|---------|-----------|----------|-------|------------|------------|-------------------|
| 1 | **llama.cpp pure** (b8892) | F16 | CPU (bare metal) | 4 | **9.4 req/s** | **5.27s** | variable |
| 2 | **vLLM** (RHOAI GPU) | FP16 | **Tesla T4 16GB** | 4 + GPU | **6.3 req/s** | **7.88s** | **1.44s / 1.64s** |
| 3 | ramalama (Vulkan) | Q4_K_M | iGPU Intel Arc MTL | 11 | 5.6 req/s | 8.81s | variable |
| 4 | ramalama (CPU) | Q4_K_M | CPU (bare metal) | 11 | 4.6 req/s | 10.73s | variable |
| 5 | ramalama (CPU) | F16 | CPU (bare metal) | 4 | 4.3 req/s | 11.55s | variable |
| 6 | ramalama (CPU) | Q4_K_M | CPU (bare metal) | 4 | 3.9 req/s | 12.82s | variable |
| 7 | **vLLM** (RHOAI CPU) | FP16 | CPU (cluster) | 8 | 3.7 req/s | 13.17s | 1.06s / 5.58s |
| 8 | **vLLM** (MicroShift) | FP16 | CPU (container) | 4 | 1.6 req/s | 29.53s | 1.79s / 14.30s |
| 9 | **vLLM** (Kind) | FP16 | CPU (container) | 4 | 1.2 req/s | 41.32s | 1.37s / 14.90s |
| 10 | **vLLM** (RHOAI CPU) | FP16 | CPU (cluster) | 4 | 1.1 req/s | 43.77s | 0.80s / 30.27s |

### Qwen2.5-3B-Instruct Results (10 concurrent, 50 requests)

The exploratory benchmark also used **[Qwen2.5-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)** (3.09B parameters, 5.8 GiB in FP16). Hardware and serving configuration differed between the following runs:

| # | Runtime | Precision | Hardware | Cores | Throughput | Wall Clock | Latency (min/max) |
|---|---------|-----------|----------|-------|------------|------------|-------------------|
| 1 | **vLLM** (RHOAI GPU) | FP16 | **Tesla T4 16GB** | 4 + GPU | **8.6 req/s** | **5.76s** | **0.60s / 1.37s** |
| 2 | **llama.cpp pure** (b8892) | F16 | CPU (bare metal) | 4 | 1.0 req/s | 47.63s | 3.52s / 4.94s |

The recorded wall times differ by about 8.3x (47.63 / 5.76). Dividing the displayed,
truncated request rates gives a different ratio and should not be used as an
independent speedup measurement. These runs compare complete configurations, not
the engines on matched hardware. They do not isolate the effect of model size,
PagedAttention, or continuous batching.

### Key Takeaways

**Workload and configuration matter.** Model size alone does not determine which
engine is faster. Compare the same hardware, precision, request lengths, cache
policy, and concurrency before attributing a difference to an engine feature.

**Latency needs a distribution.** The recorded minimum and maximum are not enough
to establish near-constant latency or an SLO. Retain successful request counts,
p50/p95/p99, throughput, and repeated runs under a representative workload.

The historical CPU configurations reported different timings; those observations
need repetition with validated responses before drawing scaling conclusions.

PagedAttention manages attention KV-cache blocks. Its usefulness depends on the
serving workload; it is not limited to models whose weights exceed GPU memory,
and it does not guarantee freedom from out-of-memory errors.

### Kind vs MicroShift (vLLM only)

**Benchmark: 5 concurrent, 10 requests**

| Metric | Kind | MicroShift |
|--------|------|------------|
| Avg Latency | 4.484s | 4.275s |
| Min/Max Latency | 1.295s / 8.816s | 1.695s / 7.341s |
| Wall Clock Time | 10.82s | 10.74s |
| Throughput | 0.9 req/s | 0.9 req/s |

**Benchmark: 10 concurrent, 50 requests**

| Metric | Kind | MicroShift |
|--------|------|------------|
| Avg Latency | 7.462s | 5.577s |
| Min/Max Latency | 1.370s / 14.897s | 1.794s / 14.298s |
| Wall Clock Time | 41.32s | 29.53s |
| Throughput | 1.2 req/s | 1.6 req/s |

### Interpreting local and cluster results

Several variables differ between these exploratory configurations:

1. **Hardware and backend** - CPU and GPU execution use different builds and kernels. vLLM CPU installation is an explicit supported-backend choice, not a universal transparent CUDA fallback.

2. **Transport and deployment** - Local Kubernetes, serverless, and direct
   serving can use different network paths. Verify the path for each run before
   attributing latency to a gateway or activator. The current RHOAI lab forwards
   directly to a Ready predictor Pod and does not benchmark an external route.

3. **Model and request size** - Weight size, prompt length, generated-token count, and concurrency affect the balance of compute, memory, and scheduling costs. The measurements do not establish that the model fits in CPU hardware cache or that KV-cache management has no benefit.

4. **Kernel implementations** - Both projects have backend-specific optimizations. Attribute an observed difference to a particular kernel or overhead only with profiling evidence.

5. **Version and launch settings** - The llama.cpp and ramalama runs used different builds and launch settings. Their separate contributions were not isolated.

### Where vLLM Shines

vLLM is a useful option for high-concurrency serving. Selection should be based on
your workload and operational requirements, not a universal ranking. The
[llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
also documents GPU execution, continuous batching, monitoring, and speculative
decoding. Compare the exact versions being deployed.

| Capability | vLLM | llama.cpp |
|------------|------|-----------|
| **GPU inference** | Multiple accelerator backends and parallelism strategies | CUDA and other backends; CPU/GPU hybrid options |
| **Memory management** | Paged KV cache; capacity still depends on weights, activations, and requests | GGUF formats, quantization, and configurable CPU/GPU placement |
| **Batching** | Continuous batching with configurable scheduler limits | Continuous batching with configurable slots |
| **Latency** | Must be measured against a workload and SLO | Must be measured against the same workload and SLO |
| **Serving features** | Prefix caching, speculative decoding, adapters; version/model dependent | Prompt reuse, speculative decoding, monitoring; version/model dependent |
| **Platform integration** | Packaged runtimes in platforms such as KServe/OpenShift AI | Can be containerized; integration and support must be evaluated separately |

**Evaluation starting points:** llama.cpp is worth testing for local, edge and
CPU/GPU-hybrid workloads; vLLM with KServe is worth testing for managed,
concurrent GPU serving. Neither is an exclusive category or an automatic winner.

> **Note:** The historical GPU/CPU comparison does not establish how either
> engine would perform on matched hardware, nor predict a speedup for larger
> models. Use a controlled benchmark and quality evaluation for that decision.

<img width="1536" height="1024" alt="Slide 11_2" src="https://github.com/user-attachments/assets/ea1bdd01-ecfd-40ba-b953-af28b55718a3" />

---

## Option C: Red Hat OpenShift AI (RHOAI) with GPU

The [RHOAI lab](rhoai/README.md) uses KServe, a versioned vLLM runtime, and a
PVC-backed Qwen2.5-3B-Instruct model. This is an educational GPU-serving lab, not
a production security or capacity-planning reference. Model licenses and
supported runtime/hardware combinations must be checked independently.

```bash
cd rhoai/
./00-run-lab.sh up vllm-demo
export PROJECT=vllm-demo
./04-run-inference.sh single
STREAM=1 ./04-run-inference.sh benchmark 4 12
./04-run-inference.sh prefix
./04-run-inference.sh metrics
```

The orchestrator resolves the installed runtime image and waits for model
readiness. The client validates responses and records actual token counts.
Non-streaming latency is not TTFT; use streaming for client-observed first-text
timing. Compare throughput and latency on matched hardware and workloads.

For a disposable end-to-end test, use a **new** project name:

```bash
./00-run-lab.sh verify vllm-lifecycle-check
```

This creates its own project, downloads the model, deploys, runs all client modes,
and deletes the project only after successful checks. A failure preserves
resources for inspection. For a retained lab, cleanup asks for confirmation:

```bash
./05-cleanup.sh vllm-demo
```

See the [RHOAI instructions](rhoai/README.md) for storage topology, immutable
model revisions, license terms, multi-GPU settings, measurement methodology,
manual steps and troubleshooting. The historical Gemma/T4 observations above
do not define the configuration or support scope of this workflow.

For the original Gemma/T4 setup, see the
[historical appendix](#historical-appendix-gemma-3-270m-on-tesla-t4-with-rhoai).

---

## Component Breakdown

* **Kind Cluster** - Lightweight Kubernetes testbed, running locally via Podman.
* **MicroShift (minc)** - Single-node OpenShift running as a Podman container, providing an OpenShift-compatible environment.
* **KServe** - Kubernetes-native model serving platform that handles full model lifecycle, scaling, and routing.
* **vLLM Backend** - Delivers fast, efficient inference with optimizations like KV cache memory management (PagedAttention), speculative decoding, and continuous batching.
* **Gemma-3-270m** - The gated model used by the Kind/MicroShift examples and historical T4 appendix; requires authorized Hugging Face access.
* **Qwen2.5-3B-Instruct** - The default model for the maintained [OpenShift AI lab](rhoai/README.md); review its separate model license and pinned revision there.

---

## Troubleshooting & Tips

### General

* The **first inference** may be slower due to model download & cold start.
* Watch the pods and logs if something fails:

  ```bash
  kubectl get pods   # (or oc get pods for MicroShift)
  kubectl logs -l serving.kserve.io/inferenceservice=gemma-3-270m -c predictor
  ```

### HuggingFace Xet download crash (SIGSEGV / exit code 139)

Starting with `kserve/huggingfaceserver:v0.19.0+` (which bundles `huggingface_hub` 1.x), the [Xet storage backend](https://huggingface.co/docs/hub/en/xet/using-xet-storage) is enabled by default for model downloads. The Xet native client (`hf-xet`) can cause a **segmentation fault (exit code 139)** on certain CPUs - notably Intel Meteor Lake and other architectures where the Xet binary has [known compatibility issues](https://github.com/huggingface/xet-core/issues/763).

**Symptoms:** The pod enters `CrashLoopBackOff` with exit code 139. Logs stop after `Final IR op priority after setting platform defaults` with no Python traceback (the crash happens in native code during model/tokenizer download).

**Fix:** Add `HF_HUB_DISABLE_XET=1` to the InferenceService env vars to fall back to standard HTTP downloads:

```yaml
env:
  - name: HF_HUB_DISABLE_XET
    value: "1"
```

### CPU attention backend (`VLLM_ATTENTION_BACKEND`)

The `VLLM_ATTENTION_BACKEND=CPU_ATTN` environment variable was used in older versions to force the CPU attention backend. It has been **deprecated in vLLM 0.13.0** ([PR #26315](https://github.com/vllm-project/vllm/releases)) and replaced by `--attention-config.backend`. In newer versions, vLLM auto-detects the CPU platform and selects the correct backend automatically - no env var is needed.

| KServe image version | Bundled vLLM | `VLLM_ATTENTION_BACKEND` status |
|----------------------|--------------|-------------------------------|
| v0.15.x | 0.8.5 | Required for CPU inference |
| v0.16.0 | 0.9.2 | Supported (last version where it's recognized) |
| v0.17.0+ | 0.15.1+ | Deprecated (generates warning, auto-detects CPU) |
| v0.19.0+ / latest | 0.20.0 | Removed (reported as "Unknown", ignored) |

**For v0.16.0 and earlier**, keep `VLLM_ATTENTION_BACKEND=CPU_ATTN` in your manifest. **For v0.17.0+**, remove it - vLLM handles CPU detection automatically.

### Kind-specific

* Ensure your **Kind cluster is running** before deploying.
* If Kind CLI isn't found immediately after Podman install, restart your shell to refresh paths.
* Use Podman Desktop's UI to **monitor cluster status**, view logs, and switch contexts easily.
* Do **not** use `sudo` with Kind commands - Kind uses Podman rootless.

### MicroShift-specific

* **Disconnect from VPN** before creating the cluster or running workloads.
* For rootful mode, `minc` commands require `sudo`. Rootless mode (via Podman Desktop) does not.
* If DNS fails inside the container (common with rootful Podman and corporate DNS), configure `/etc/containers/containers.conf.d/dns.conf` with public DNS servers.
* Use a separate `KUBECONFIG` to avoid conflicts with Kind:

  ```bash
  export KUBECONFIG=~/.kube/config-microshift
  ```

* If the Istio ingressgateway pod fails with SCC errors, grant the `anyuid` SCC:

  ```bash
  oc adm policy add-scc-to-user anyuid -z istio-ingressgateway -n istio-system
  ```

---

## References

* [Kind Documentation](https://kind.sigs.k8s.io/docs/user/quick-start/)
* [Kind CLI detection: auto-detects Podman runtime when env var is set](https://kind.sigs.k8s.io/docs/user/quick-start)
* [Podman + Kind setup](https://podman-desktop.io/docs/kind)
* [Podman Desktop Kind integration: Create and manage Kind clusters via UI](https://podman-desktop.io/docs/kind/installing-extension)
* [Podman Desktop resources settings: Kind tile creation](https://podman-desktop.io/docs/kind/creating-a-kind-cluster)
* [Switching Kubernetes context in UI: Podman Desktop -> Kubernetes menu](https://podman-desktop.io/docs/kind/working-with-your-local-kind-cluster)
* [MicroShift Documentation](https://microshift.io/)
* [minc - MicroShift in a Container](https://github.com/minc-org/minc)
* [KServe Documentation](https://kserve.github.io/website/docs/intro)
* [KServe's Hugging Face & vLLM Integration](https://kserve.github.io/website/docs/model-serving/generative-inference/overview)
* [Red Hat OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
* [vLLM Documentation](https://docs.vllm.ai/)
* [ramalama - Run AI Models Locally](https://github.com/containers/ramalama)
* [llama.cpp](https://github.com/ggml-org/llama.cpp)
* [Gemma-3-270m Model](https://huggingface.co/google/gemma-3-270m)
* [Qwen2.5-3B-Instruct Model](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)
* [Hugging Face Security Tokens](https://huggingface.co/docs/hub/en/security-tokens)

---

## Historical Appendix: Gemma-3-270m on Tesla T4 with RHOAI

> **Historical reference only.** This appendix records the Gemma-3-270m/Tesla T4
> configuration from commit `edb8e72`, including the original upstream
> `vllm/vllm-openai:v0.8.5` custom container. These instructions have not been
> executed or retested as part of the current validation. That validation does
> not establish compatibility or support for this historical configuration.
> New users should start with the maintained [RHOAI workflow](rhoai/README.md)
> in [`rhoai/`](rhoai/).

The following five steps preserve the original project, PVC, download Job,
InferenceService, request parameters, and legacy benchmark invocation for
historical traceability. They are archival examples, not a currently validated
deployment recipe. The custom-container predictor below does not select the
`ServingRuntime` used by the maintained workflow. Credentials are placeholders;
filesystem paths in the manifests refer to locations inside the containers.

### Historical prerequisites

- An RHOAI cluster with KServe and a Tesla T4 GPU available to the workload.
- An installed, authenticated `oc` client and permission to create the resources.
- Access to `google/gemma-3-270m`, acceptance of its terms, and a Hugging Face token.
- `curl` and `jq` for the request example; Bash, `bc`, and the command-line
  utilities used by the legacy [benchmark script](llamacpp-bench.sh).

### Step 1: Create a Project

```bash
oc new-project vllm-benchmark
```

### Step 2: Download the Model via a Job

The original setup downloaded the model into a PVC using a separate Job with a
mounted token Secret. It then disabled the serving Pod's storage initializer and
mounted that PVC directly. This records the chosen download path; it does not
imply that all RHOAI installations require this workaround.

```bash
# Placeholder only; no credential is included in this example.
oc create secret generic huggingface-secret --from-literal=token='<your_token>'

# Create PVC for model storage
cat <<EOF | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: gemma-model-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
EOF

# Download the model
cat <<'EOF' | oc apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: download-model
spec:
  template:
    spec:
      containers:
        - name: downloader
          image: registry.access.redhat.com/ubi9/python-311:latest
          command:
            - bash
            - -c
            - |
              pip install -q huggingface_hub && \
              python -c "
              from huggingface_hub import snapshot_download
              snapshot_download(
                  'google/gemma-3-270m',
                  local_dir='/mnt/models',
                  token='$(cat /secret/token)'
              )
              print('Download complete!')
              "
          volumeMounts:
            - name: model-storage
              mountPath: /mnt/models
            - name: hf-token
              mountPath: /secret
              readOnly: true
          resources:
            requests:
              cpu: "1"
              memory: "2Gi"
            limits:
              cpu: "2"
              memory: "4Gi"
      volumes:
        - name: model-storage
          persistentVolumeClaim:
            claimName: gemma-model-pvc
        - name: hf-token
          secret:
            secretName: huggingface-secret
      restartPolicy: Never
  backoffLimit: 2
EOF

# Original manual observation of download completion
oc get pods -w
```

This example did not pin a model revision, the downloader image digest, or the
`huggingface_hub` version. It did not select a storage class or GPU zone. Its
resource settings therefore do not guarantee a reproducible download or
compatible volume placement on another cluster. Watching Pods is not an
automated check of Job success.

### Step 3: Deploy vLLM with GPU

The historical InferenceService used RawDeployment and a directly specified
vLLM 0.8.5 container, with FP16, eager execution, and one requested GPU:

```bash
cat <<'EOF' | oc apply -f -
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: gemma-3-270m-gpu
  annotations:
    serving.kserve.io/deploymentMode: RawDeployment
    serving.kserve.io/disableStorageInitializer: "true"
spec:
  predictor:
    tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
    containers:
      - name: kserve-container
        image: vllm/vllm-openai:v0.8.5
        args:
          - --port=8080
          - --model=/mnt/models
          - --served-model-name=gemma-3-270m
          - --dtype=half
          - --enforce-eager
        env:
          - name: HOME
            value: /tmp
          - name: TRITON_CACHE_DIR
            value: /tmp/.triton
        ports:
          - containerPort: 8080
            protocol: TCP
        volumeMounts:
          - name: model-storage
            mountPath: /mnt/models
            readOnly: true
        resources:
          requests:
            cpu: "4"
            memory: "8Gi"
            nvidia.com/gpu: "1"
          limits:
            cpu: "8"
            memory: "16Gi"
            nvidia.com/gpu: "1"
    volumes:
      - name: model-storage
        persistentVolumeClaim:
          claimName: gemma-model-pvc
EOF
```

| Historical setting | Interpretation and limitation |
|--------------------|-------------------------------|
| `vllm/vllm-openai:v0.8.5` | Original upstream custom-container image, not the installed RHOAI runtime selected by the maintained lab. Its model/runtime compatibility has not been revalidated. |
| GPU toleration | Permits the matching taint; it does not select a Tesla T4, guarantee available capacity, or resolve PVC topology constraints. |
| `--dtype=half` | Selects FP16 for the T4 configuration; automatic dtype selection must not be assumed to make a BF16 model compatible with T4. |
| `--enforce-eager` | Disables CUDA-graph execution. It is not a general fix for Triton compilation or filesystem permissions. |
| `HOME` and `TRITON_CACHE_DIR` | Point to writable locations inside the container; their actual permissions still depend on the image and cluster security configuration. |
| Disabled storage initializer | Relies on the separate download Job and mounted PVC; it does not verify that the model download succeeded. |

### Step 4: Run the Benchmark

The original sequence observed Pods, forwarded a predictor Pod to loopback, and
sent a non-streaming completion request. The Pod selector and manual readiness
step are retained as historical details, not current readiness guarantees.

```bash
# Original manual readiness observation
oc get pods -w

# Port-forward to the pod
POD=$(oc get pod -l app=gemma-3-270m-gpu-predictor -o name)
oc port-forward "$POD" 8080:8080 &

# Single request test
curl -s http://localhost:8080/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-3-270m",
    "prompt": "Being an IT professional is",
    "stream": false,
    "max_tokens": 30,
    "stop": ["."]
  }' | jq .

# Original legacy harness invocation from the repository root
./llamacpp-bench.sh benchmark 10 50
```

**Legacy timing-only harness limitations:** In benchmark mode, the original
`llamacpp-bench.sh` discarded response bodies and recorded curl timing values
without retaining HTTP status, validating completions, or counting actual output
tokens per request. Failed requests could contribute to the displayed request
rate. Its llama.cpp labels and `/props` probe did not verify which engine served
the requests. Non-streaming time to first byte is not token-level TTFT, and
`max_tokens: 30` with a stop string does not guarantee 30 generated tokens.

These limitations apply to the [historical performance tables](#performance-comparison-vllm-vs-llamacpp).
Different hardware, precisions, runtime versions, and serving paths prevent
those results from isolating engine effects. The comparison does not prove
vLLM or llama.cpp superiority, a model-size threshold for GPU benefits, or the
causal effect of PagedAttention or continuous batching. Use the maintained
[RHOAI measurement workflow](rhoai/README.md#measurement-and-a-practical-demo)
for new experiments.

### Step 5: Clean Up

The original cleanup deleted the entire example project, including its
namespaced resources. It did not manage the background port-forward process,
which must be stopped separately.

```bash
oc delete project vllm-benchmark
```
