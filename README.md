# vLLM + KServe Demo

This project demonstrates deploying and testing LLM inference using **KServe** with **vLLM** as the runtime. Benchmarked models include **[Gemma-3-270m](https://huggingface.co/google/gemma-3-270m)** (270M params) and **[Qwen2.5-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)** (3.4B params). Three environments are supported:

- **[Kind](kind/)** - Lightweight Kubernetes cluster using Podman Desktop
- **[MicroShift](microshift/)** - Single-node OpenShift (via [minc](https://github.com/minc-org/minc)) for an OpenShift-compatible experience
- **[Red Hat OpenShift AI (RHOAI)](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)** - Enterprise OpenShift with GPU support (Tesla T4)

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

All tests used identical request parameters (prompt: *"Being an IT professional is"*, max 30 tokens, stop on `.`). Local tests ran on the same machine (CPU-only, no NVIDIA GPU). The RHOAI tests ran on an OpenShift cluster in AWS with a Tesla T4 GPU.

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

To validate the GPU advantage with a larger model, the same benchmark was repeated using **[Qwen2.5-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)** (3.4B parameters, 5.8 GiB in FP16) - a model 12.5x larger than Gemma-3-270m:

| # | Runtime | Precision | Hardware | Cores | Throughput | Wall Clock | Latency (min/max) |
|---|---------|-----------|----------|-------|------------|------------|-------------------|
| 1 | **vLLM** (RHOAI GPU) | FP16 | **Tesla T4 16GB** | 4 + GPU | **8.6 req/s** | **5.76s** | **0.60s / 1.37s** |
| 2 | **llama.cpp pure** (b8892) | F16 | CPU (bare metal) | 4 | 1.0 req/s | 47.63s | 3.52s / 4.94s |

**GPU advantage: 8.6x throughput, 8.3x faster wall clock time.** With the larger model, vLLM on a Tesla T4 delivered 8.6 requests/second while llama.cpp on CPU managed only 1.0 req/s. The GPU was also *faster* with the 3B model (8.6 req/s) than with the 270M model (6.3 req/s), because larger models better utilize GPU batch parallelism through continuous batching.

### Key Takeaways

**Model size determines the GPU advantage.** With the tiny 270M model, llama.cpp on CPU was faster than vLLM on GPU (9.4 vs 6.3 req/s). With the 3B model, the roles reversed dramatically: vLLM GPU was 8.6x faster. This confirms that vLLM's GPU optimizations (PagedAttention, continuous batching) only pay off when the model is large enough to benefit from GPU memory management.

**vLLM GPU stands out for latency consistency.** The vLLM GPU results show remarkably consistent latency under load: **0.60s-1.37s** (3B model) and **1.44s-1.64s** (270M model) across all 50 requests. Every other runtime showed significant latency variance under concurrency.

**vLLM scales with CPU cores.** On the RHOAI cluster, going from 4 to 8 CPU cores improved throughput from 1.1 to 3.7 req/s (3.4x improvement), approaching ramalama CPU performance levels.

**With a tiny model (270M), this is vLLM's worst case.** The model fits entirely in CPU cache, so vLLM's GPU memory optimizations provide no benefit. The 3B model results show the true picture for production workloads.

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
| **Latency consistency** | Near-constant latency under load (see GPU results above) | Latency varies significantly with concurrency |
| **Production features** | LoRA hot-swap, speculative decoding, prefix caching | Basic serving |
| **Kubernetes integration** | KServe: autoscaling, canary rollouts, model versioning, A/B testing | Manual deployment |
| **Multi-model serving** | Multiple LoRA adapters on a single base model | One model per server instance |

**Rule of thumb:**
- **Local development, edge, CPU, or single-user** -> llama.cpp
- **Production, GPU cluster, multi-user, enterprise** -> vLLM + KServe

> **Note:** The Qwen2.5-3B benchmark above demonstrates this in practice: vLLM GPU achieved 8.6x higher throughput than llama.cpp CPU. With even larger models (13B+), the GPU advantage grows further as PagedAttention and continuous batching fully utilize GPU parallelism.

---

## Option C: Red Hat OpenShift AI (RHOAI) with GPU

If you have access to a [Red Hat OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai) cluster with NVIDIA GPUs, you can test vLLM in its intended production environment. This section documents how the RHOAI GPU benchmark was performed.

### Prerequisites

1. **RHOAI cluster** with KServe and GPU nodes available
2. **oc CLI** installed and logged in
3. **Hugging Face token** with access to [google/gemma-3-270m](https://huggingface.co/google/gemma-3-270m)

### Step 1: Create a Project

```bash
oc new-project vllm-benchmark
```

### Step 2: Download the Model via a Job

Since KServe's storage initializer may not have access to your HuggingFace token (it requires ClusterStorageContainer configuration), download the model to a PVC using a Job:

```bash
# Create HuggingFace secret
oc create secret generic huggingface-secret --from-literal=token=<your_token>

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

# Wait for download to complete
oc get pods -w
```

### Step 3: Deploy vLLM with GPU

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

> **Note:** If the cluster uses a MachineAutoscaler for GPU nodes, the first deploy may take up to 15 minutes while a new GPU instance is provisioned.

### Step 4: Run the Benchmark

```bash
# Wait for the pod to be ready
oc get pods -w

# Port-forward to the pod
POD=$(oc get pod -l app=gemma-3-270m-gpu-predictor -o name)
oc port-forward $POD 8080:8080 &

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

# Or use the benchmark script
./llamacpp-bench.sh benchmark 10 50
```

### Step 5: Clean Up

```bash
oc delete project vllm-benchmark
```

### RHOAI Deployment Notes

| Topic | Details |
|-------|---------|
| **GPU taints** | GPU nodes typically have taints like `nvidia.com/gpu: Tesla-T4-SHARED`. Use `tolerations` with `operator: Exists` to schedule on any available GPU node. |
| **`--dtype=half`** | Required for Tesla T4 (compute capability 7.5). T4 does not support bfloat16 - use float16 instead. |
| **`--enforce-eager`** | Avoids Triton compilation issues with OpenShift's restricted filesystem. |
| **`HOME=/tmp`** | OpenShift runs containers as non-root with a random UID. Set HOME and TRITON_CACHE_DIR to writable paths. |
| **Storage initializer** | Use `serving.kserve.io/disableStorageInitializer: "true"` and mount the model via PVC to avoid HuggingFace token issues with the init container. |
| **CPU-only alternative** | Replace `vllm/vllm-openai:v0.8.5` with `kserve/huggingfaceserver:v0.15.2`, remove GPU resources/tolerations, and use `--model_dir=/mnt/models` instead of `--model`. The OpenAI endpoints will be at `/openai/v1/` instead of `/v1/`. |

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
* [Red Hat OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
* [vLLM Documentation](https://docs.vllm.ai/)
* [ramalama - Run AI Models Locally](https://github.com/containers/ramalama)
* [llama.cpp](https://github.com/ggml-org/llama.cpp)
* [Gemma-3-270m Model](https://huggingface.co/google/gemma-3-270m)
* [Qwen2.5-3B-Instruct Model](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)
* [Hugging Face Security Tokens](https://huggingface.co/docs/hub/en/security-tokens)

---
