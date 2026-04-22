# vLLM + KServe Local Demo

This project demonstrates deploying and testing the **[Gemma-3-270m](https://huggingface.co/google/gemma-3-270m)** model locally using **KServe** with **vLLM** as the inference runtime. Two local Kubernetes environments are supported:

- **[Kind](kind/)** - Lightweight Kubernetes cluster using Podman Desktop
- **[MicroShift](microshift/)** - Single-node OpenShift (via [minc](https://github.com/minc-org/minc)) for an OpenShift-compatible experience

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
├── llamacpp-bench.sh               # llama.cpp server benchmark script
└── README.md
```

---

## Prerequisites

1. **Hugging Face token** - Accept the model's terms and generate a user access token: [Hugging Face tokens guide](https://huggingface.co/docs/hub/en/security-tokens)

2. **A local Kubernetes cluster** - Choose one of the two options below.

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

All tests ran on the same machine (ThinkPad P1 Gen7, CPU-only, no NVIDIA GPU) with the **Gemma-3-270m** model and identical request parameters (prompt: *"Being an IT professional is"*, max 30 tokens, stop on `.`).

### Full Results (10 concurrent, 50 requests)

| # | Runtime | Precision | Hardware | Threads | Throughput | Wall Clock | vs Best |
|---|---------|-----------|----------|---------|------------|------------|---------|
| 1 | **llama.cpp pure** (b8892) | F16 | CPU | 4 | **9.4 req/s** | **5.27s** | - |
| 2 | ramalama (Vulkan) | Q4_K_M | iGPU Intel Arc MTL | 11 | 5.6 req/s | 8.81s | -40% |
| 3 | ramalama (CPU) | Q4_K_M | CPU | 11 | 4.6 req/s | 10.73s | -51% |
| 4 | ramalama (CPU) | F16 | CPU | 4 | 4.3 req/s | 11.55s | -54% |
| 5 | ramalama (CPU) | Q4_K_M | CPU | 4 | 3.9 req/s | 12.82s | -59% |
| 6 | **vLLM** (MicroShift) | FP16 | CPU (container, 4 cores) | 4 | 1.6 req/s | 29.53s | -83% |
| 7 | **vLLM** (Kind) | FP16 | CPU (container, 4 cores) | 4 | 1.2 req/s | 41.32s | -87% |

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

### Why llama.cpp Wins on Local/CPU

These results reflect a **worst-case scenario for vLLM** and a **best-case scenario for llama.cpp**:

1. **No NVIDIA GPU** - vLLM is built on PyTorch + CUDA. Without a GPU, it falls back to CPU inference and loses its primary advantage (PagedAttention, GPU memory management, tensor parallelism).

2. **Container overhead** - vLLM runs inside a Kubernetes pod behind Istio/Knative routing layers. Each request passes through: client -> port-forward -> Istio gateway -> Knative activator -> vLLM container. llama.cpp runs as a bare-metal process with direct socket access.

3. **Small model** - Gemma-3-270m (540MB in FP16) fits entirely in CPU cache. vLLM's memory optimizations (PagedAttention, KV cache paging) provide no benefit at this scale - they are designed for models that exceed GPU VRAM.

4. **llama.cpp's CPU optimizations** - llama.cpp is written in C/C++ with hand-tuned SIMD kernels (AVX2, AVX-512, VNNI) that directly target the CPU's instruction set. vLLM uses PyTorch's general-purpose CPU backend, which carries significant overhead for small models.

5. **Pure llama.cpp vs ramalama** - The pure build (b8892) is 2.2x faster than ramalama's bundled version (b1-75f3bc9) due to a newer llama.cpp version, `kv_unified=false` (better memory layout), and optimized per-slot context allocation.

### Where vLLM Shines

vLLM's architecture is designed for **production GPU serving at scale**, where it significantly outperforms llama.cpp:

| Capability | vLLM | llama.cpp |
|------------|------|-----------|
| **GPU inference (NVIDIA)** | Native CUDA, tensor parallelism across multiple GPUs | Limited GPU support (Vulkan, some CUDA) |
| **Large models (7B-405B)** | PagedAttention manages GPU VRAM efficiently, no OOM | Must fit entirely in memory or use manual layer splitting |
| **Continuous batching** | Dynamic batching across hundreds of concurrent requests | Fixed slot count, limited parallelism |
| **Production features** | LoRA hot-swap, speculative decoding, prefix caching | Basic serving |
| **Kubernetes integration** | KServe: autoscaling, canary rollouts, model versioning, A/B testing | Manual deployment |
| **Multi-model serving** | Multiple LoRA adapters on a single base model | One model per server instance |

**Rule of thumb:**
- **Local development, edge, CPU, or single-user** -> llama.cpp
- **Production, GPU cluster, multi-user, enterprise** -> vLLM + KServe

> **Note:** On GPU hardware (e.g., NVIDIA A100), vLLM's throughput for large models (13B+) is typically **10-100x** higher than llama.cpp, because PagedAttention and continuous batching fully utilize GPU parallelism. The CPU-only results above do not reflect vLLM's intended operating environment.

---

## Component Breakdown

* **Kind Cluster** - Lightweight Kubernetes testbed, running locally via Podman.
* **MicroShift (minc)** - Single-node OpenShift running as a Podman container, providing an OpenShift-compatible environment.
* **KServe** - Kubernetes-native model serving platform that handles full model lifecycle, scaling, and routing.
* **vLLM Backend** - Delivers fast, efficient inference with optimizations like KV cache memory management (PagedAttention), speculative decoding, and continuous batching.
* **Gemma-3-270m** - The Hugging Face model you're serving; requires token-based authentication from a Hugging Face account.

---

## Troubleshooting & Tips

### General

* The **first inference** may be slower due to model download & cold start.
* Watch the pods and logs if something fails:

  ```bash
  kubectl get pods   # (or oc get pods for MicroShift)
  kubectl logs -l serving.kserve.io/inferenceservice=gemma-3-270m -c predictor
  ```

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
* [Gemma-3-270m Model](https://huggingface.co/google/gemma-3-270m)
* [Hugging Face Security Tokens](https://huggingface.co/docs/hub/en/security-tokens)

---
