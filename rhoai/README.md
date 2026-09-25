# vLLM on Red Hat OpenShift AI (RHOAI) with GPU

This directory contains scripts and manifests for deploying and benchmarking **vLLM** on **Red Hat OpenShift AI** with NVIDIA GPU acceleration. It demonstrates the full workflow: model download, serving runtime configuration, inference, and performance benchmarking.

While the [Kind](../kind/) and [MicroShift](../microshift/) environments run vLLM on CPU, this setup uses RHOAI's built-in KServe integration with GPU-backed serving runtimes - the way vLLM is designed to run in production.

---

## Directory Structure

```
rhoai/
├── 01-setup-project.sh          # Create project, PVC, download model
├── 02-serving-runtime.yaml      # vLLM CUDA ServingRuntime for KServe
├── 03-inference-service.yaml    # InferenceService with GPU (RawDeployment)
├── 04-run-inference.sh          # Inference, benchmark, and metrics
├── 05-cleanup.sh                # Remove all resources
└── README.md
```

---

## Prerequisites

1. **RHOAI cluster** with the following operators installed and configured:
   - Red Hat OpenShift AI (with KServe managed)
   - NVIDIA GPU Operator
   - OpenShift Serverless (Knative)
   - OpenShift Service Mesh

2. **At least one GPU node** with an NVIDIA GPU (L4, T4, A100, H100, etc.)

3. **`oc` CLI** installed and logged in as a user with project creation permissions

4. **Tools:** `curl`, `jq`, `bc` (for the benchmark script)

---

## Quick Start

```bash
cd rhoai/

# Step 1: Create the project and download the model
./01-setup-project.sh

# Step 2: Deploy the vLLM serving runtime
oc apply -f 02-serving-runtime.yaml

# Step 3: Deploy the InferenceService
oc apply -f 03-inference-service.yaml

# Step 4: Wait for the model to load (~2-5 min depending on GPU)
oc get pods -w

# Step 5: Run inference
./04-run-inference.sh

# Step 6: Run benchmark
./04-run-inference.sh benchmark 10 50
```

---

## Detailed Steps

### Step 1: Set Up the Project

The setup script creates a Data Science project, provisions a PVC for model storage, and downloads the model weights via a Kubernetes Job.

```bash
# Default: creates project "vllm-demo" with Qwen2.5-3B-Instruct
./01-setup-project.sh

# Custom project name
./01-setup-project.sh my-project

# With HuggingFace token (required for gated models like Llama, Gemma)
HF_TOKEN=hf_xxxxx ./01-setup-project.sh

# Custom model (adjust PVC_SIZE for larger models)
MODEL_ID=meta-llama/Llama-3.2-3B-Instruct PVC_SIZE=20Gi HF_TOKEN=hf_xxx ./01-setup-project.sh
```

The model download runs as a Job inside the cluster. For **Qwen2.5-3B-Instruct** (~6.1 GB), expect 5-15 minutes depending on network speed.

### Step 2: Deploy the ServingRuntime

```bash
oc apply -f 02-serving-runtime.yaml
```

This creates a vLLM CUDA serving runtime that:
- Uses the RHOAI-provided vLLM container image with CUDA support
- Exposes the OpenAI-compatible API on port 8080
- Publishes Prometheus metrics at `/metrics`

### Step 3: Deploy the InferenceService

```bash
oc apply -f 03-inference-service.yaml
```

The InferenceService:
- Uses **RawDeployment** mode (always running, no cold starts)
- Requests **1 NVIDIA GPU** with tolerations for GPU node taints
- Mounts the pre-downloaded model from the PVC
- Configures vLLM with `--dtype=auto` (auto-detects GPU capabilities)
- Sets `--max-model-len=4096` and `--enforce-eager` for stability

Wait for the predictor pod to be ready:

```bash
# Watch pod status
oc get pods -w

# Check vLLM startup logs
oc logs -l app=qwen25-3b-predictor -c kserve-container --tail=20
```

The pod will show `Running 1/1` once vLLM has loaded the model into GPU memory.

### Step 4: Run Inference and Benchmarks

The inference script auto-discovers the predictor pod and sets up port-forwarding:

```bash
# Single inference request
./04-run-inference.sh

# Benchmark: 5 concurrent, 10 total (default)
./04-run-inference.sh benchmark

# Benchmark: 10 concurrent, 50 total
./04-run-inference.sh benchmark 10 50

# Show vLLM Prometheus metrics
./04-run-inference.sh metrics
```

You can also send requests directly via `curl`:

```bash
# Port-forward to the predictor pod
POD=$(oc get pod -l app=qwen25-3b-predictor -o name)
oc port-forward $POD 8080:8080 &

# Chat completion
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen25-3b",
    "messages": [{"role": "user", "content": "What is PagedAttention?"}],
    "max_tokens": 100,
    "temperature": 0.7
  }' | jq .

# List models
curl -s http://localhost:8080/v1/models | jq .

# Prometheus metrics
curl -s http://localhost:8080/metrics | grep "vllm:"
```

### Step 5: Clean Up

```bash
./05-cleanup.sh
```

---

## Customization

### Using a Different Model

Edit `03-inference-service.yaml` to change the model name and tune resources:

| Parameter | Default | Notes |
|-----------|---------|-------|
| `--served-model-name` | `qwen25-3b` | Model name exposed via API |
| `--dtype` | `auto` | Use `half` for T4 GPUs (no BF16 support) |
| `--max-model-len` | `4096` | Context window size (affects KV cache memory) |
| `--enforce-eager` | enabled | Disables CUDA graph compilation (more stable) |
| `nvidia.com/gpu` | `1` | Increase for tensor parallelism with `--tensor-parallel-size` |

Also update `01-setup-project.sh` with `MODEL_ID` and `PVC_SIZE` environment variables.

### GPU-Specific Notes

| GPU | Compute | VRAM | `--dtype` | Notes |
|-----|---------|------|-----------|-------|
| **L4** | 8.9 | 24 GB | `auto` (BF16) | Good for models up to ~7B FP16 |
| **T4** | 7.5 | 16 GB | `half` (FP16) | No BF16 support |
| **A100** | 8.0 | 40/80 GB | `auto` (BF16) | Production standard |
| **H100** | 9.0 | 80 GB | `auto` (BF16/FP8) | Highest throughput |

### Multi-GPU (Tensor Parallelism)

For models that don't fit on a single GPU, add tensor parallelism:

```yaml
# In 03-inference-service.yaml
args:
  - --tensor-parallel-size=2
resources:
  limits:
    nvidia.com/gpu: "2"
```

### Serverless Mode (Scale to Zero)

Replace `RawDeployment` with Knative Serverless by removing the deployment mode annotation:

```yaml
# Remove these annotations:
#   serving.kserve.io/deploymentMode: RawDeployment
#   serving.kserve.io/disableStorageInitializer: "true"
```

Note: Serverless mode adds cold start latency (~30-60s) when scaling from zero.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  RHOAI Cluster                       │
│                                                      │
│  ┌──────────┐    ┌───────────┐    ┌───────────────┐  │
│  │  Client   │───>│  KServe   │───>│ vLLM Runtime  │  │
│  │(curl/SDK) │    │ (routing) │    │  (GPU pod)    │  │
│  └──────────┘    └───────────┘    │               │  │
│                                    │ ┌───────────┐ │  │
│                                    │ │ NVIDIA GPU │ │  │
│                                    │ │  (L4/T4/   │ │  │
│                                    │ │  A100/H100)│ │  │
│                                    │ └───────────┘ │  │
│                                    │ ┌───────────┐ │  │
│                                    │ │ Model PVC  │ │  │
│                                    │ │ (weights)  │ │  │
│                                    │ └───────────┘ │  │
│                                    └───────────────┘  │
│                                                      │
│  Endpoints:                                          │
│    /v1/chat/completions  - Chat API                  │
│    /v1/completions       - Text completions          │
│    /v1/models            - List models               │
│    /metrics              - Prometheus metrics        │
└──────────────────────────────────────────────────────┘
```

---

## Deployment Notes

| Topic | Details |
|-------|---------|
| **GPU taints** | GPU nodes may have taints like `nvidia.com/gpu`. The InferenceService includes `tolerations` with `operator: Exists` to schedule on any GPU node. |
| **`--enforce-eager`** | Avoids Triton compilation issues with OpenShift's restricted filesystem. Can be removed in permissive environments for better performance. |
| **`HOME=/tmp`** | OpenShift runs containers as non-root with a random UID. `HOME`, `HF_HOME`, and `TRITON_CACHE_DIR` must point to writable directories. |
| **Storage initializer** | Disabled via annotation (`serving.kserve.io/disableStorageInitializer: "true"`) because the model is pre-downloaded to a PVC. This avoids init container authentication issues. |
| **RawDeployment mode** | Bypasses Knative Serving for simpler routing and no scale-to-zero behavior. Ideal for demos and dedicated GPU workloads. |
| **Image pull** | The RHOAI vLLM image is hosted on `registry.redhat.io` and requires an active pull secret (automatically configured on RHOAI clusters). |

---

## Troubleshooting

### Pod stays in `Pending`

Check if the GPU node has capacity:

```bash
oc describe node -l nvidia.com/gpu.present=true | grep -A5 "Allocated resources"
```

### Pod stays in `ContainerCreating`

The vLLM image is large (~14 GB). First pull can take 2-5 minutes. Check events:

```bash
oc get events --sort-by='.lastTimestamp' | tail -10
```

### `ErrImagePull`

Verify the cluster has a valid pull secret for `registry.redhat.io`:

```bash
oc get secret -n openshift-config pull-secret
```

### vLLM fails to start (OOM or CUDA errors)

Check the container logs:

```bash
oc logs -l app=qwen25-3b-predictor -c kserve-container
```

Common fixes:
- **OOM:** Reduce `--max-model-len` or use a quantized model
- **CUDA error:** Ensure `--dtype=half` for T4 GPUs (no BF16 support)
- **Triton errors:** Add `--enforce-eager` to skip CUDA graph compilation

### HuggingFace download fails

For gated models, ensure the HuggingFace token secret exists and has accepted the model's license:

```bash
oc get secret huggingface-secret
```

For Xet download crashes (exit code 139), set `HF_HUB_DISABLE_XET=1` in the download job.

---

## References

- [vLLM Documentation](https://docs.vllm.ai/)
- [Red Hat OpenShift AI Documentation](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/)
- [KServe Documentation](https://kserve.github.io/website/)
- [Qwen2.5-3B-Instruct Model](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct)
- [Red Hat AI Validated Models](https://docs.redhat.com/en/documentation/red_hat_ai/3/html-single/validated_models/index)
