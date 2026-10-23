#!/bin/bash
# Set up RHOAI project with model storage for vLLM inference
# Usage:
#   ./01-setup-project.sh                          # uses default project name
#   ./01-setup-project.sh my-project               # custom project name
#   HF_TOKEN=hf_xxx ./01-setup-project.sh          # with HuggingFace token (for gated models)

set -euo pipefail

PROJECT=${1:-vllm-demo}
MODEL_ID=${MODEL_ID:-Qwen/Qwen2.5-3B-Instruct}
PVC_SIZE=${PVC_SIZE:-10Gi}
DOWNLOAD_TIMEOUT=${DOWNLOAD_TIMEOUT:-1800s}
DOWNLOAD_IMAGE=${DOWNLOAD_IMAGE:-registry.access.redhat.com/ubi9/python-311:latest}
HF_TOKEN=${HF_TOKEN:-}
if [[ $MODEL_ID == Qwen/Qwen2.5-3B-Instruct ]]; then
    MODEL_REVISION=${MODEL_REVISION:-aa8e72537993ba99e69dfaafa59ed015b17504d1}
else
    : "${MODEL_REVISION:?Set MODEL_REVISION to the full model commit SHA}"
fi
[[ $MODEL_REVISION =~ ^[0-9a-f]{40}$ ]] || { echo "MODEL_REVISION must be a full immutable commit SHA" >&2; exit 2; }
[[ $MODEL_ID =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "Invalid MODEL_ID" >&2; exit 2; }
[[ $PROJECT =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || { echo "Invalid project name" >&2; exit 2; }
command -v oc >/dev/null
oc whoami >/dev/null
STORAGE_CLASS=${STORAGE_CLASS:-$(oc get storageclass -o json | python3 -c '
import json, sys
items = json.load(sys.stdin)["items"]
defaults = [item["metadata"]["name"] for item in items
            if item["metadata"].get("annotations", {}).get("storageclass.kubernetes.io/is-default-class") == "true"]
if len(defaults) != 1:
    sys.exit("Set STORAGE_CLASS explicitly: exactly one default storage class is required")
print(defaults[0])')}
[[ $STORAGE_CLASS =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || { echo "Invalid STORAGE_CLASS" >&2; exit 2; }
BINDING_MODE=$(oc get storageclass "$STORAGE_CLASS" -o jsonpath='{.volumeBindingMode}')
[[ $BINDING_MODE == WaitForFirstConsumer ]] || { echo "STORAGE_CLASS must use WaitForFirstConsumer for GPU-aware placement" >&2; exit 2; }
# Avoid binding a zonal volume to a CPU-only zone on multi-zone clusters.
STORAGE_ZONE=${STORAGE_ZONE:-$(oc get nodes -l nvidia.com/gpu.present=true -o jsonpath='{.items[0].metadata.labels.topology\.kubernetes\.io/zone}')}
STORAGE_KEY=topology.kubernetes.io/zone
STORAGE_VALUE=$STORAGE_ZONE
if [[ -z $STORAGE_VALUE ]]; then
    STORAGE_KEY=kubernetes.io/hostname
    STORAGE_VALUE=$(oc get nodes -l nvidia.com/gpu.present=true -o jsonpath='{.items[0].metadata.labels.kubernetes\.io/hostname}')
fi
[[ $STORAGE_VALUE =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "No GPU placement found. Set STORAGE_ZONE to a zone with an available GPU node." >&2; exit 2; }

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

printf "\n📦 ${CYAN}Setting up RHOAI project for vLLM inference${NC}\n"
printf "   Project:  ${YELLOW}%s${NC}\n" "$PROJECT"
printf "   Model:    ${YELLOW}%s${NC}\n" "$MODEL_ID"
printf "   PVC Size: ${YELLOW}%s${NC}\n\n" "$PVC_SIZE"

# --- Create project ---
if oc get project "$PROJECT" &>/dev/null; then
    if [[ -n ${EXPECTED_PROJECT_UID:-} ]]; then
        ACTUAL_PROJECT_UID=$(oc get project "$PROJECT" -o jsonpath='{.metadata.uid}')
        [[ $ACTUAL_PROJECT_UID == "$EXPECTED_PROJECT_UID" ]] || { echo "Project UID changed; refusing to modify it" >&2; exit 1; }
    fi
    printf "✅ Project ${GREEN}%s${NC} already exists\n" "$PROJECT"
    oc project "$PROJECT"
else
    [[ -z ${EXPECTED_PROJECT_UID:-} ]] || { echo "Owned verification project disappeared" >&2; exit 1; }
    printf "➡️ Creating project ${YELLOW}%s${NC}...\n" "$PROJECT"
    oc new-project "$PROJECT"
fi

# Label namespace for RHOAI dashboard visibility
oc label namespace "$PROJECT" opendatahub.io/dashboard=true --overwrite

# --- Create PVC for model storage ---
if oc get pvc model-pvc -n "$PROJECT" &>/dev/null; then
    printf "✅ PVC ${GREEN}model-pvc${NC} already exists\n"
    STORED_MODEL=$(oc get pvc model-pvc -n "$PROJECT" -o jsonpath='{.metadata.annotations.demo\.vllm/model-id}')
    STORED_REVISION=$(oc get pvc model-pvc -n "$PROJECT" -o jsonpath='{.metadata.annotations.demo\.vllm/model-revision}')
    if [[ $STORED_MODEL != "$MODEL_ID" || $STORED_REVISION != "$MODEL_REVISION" ]]; then
        echo "Existing PVC belongs to a different or unrecorded model/revision. Use a new project; no data was changed." >&2
        exit 1
    fi
    STORED_CLASS=$(oc get pvc model-pvc -n "$PROJECT" -o jsonpath='{.spec.storageClassName}')
    [[ $STORED_CLASS == "$STORAGE_CLASS" ]] || { echo "Existing PVC storage class differs; use its class or a new project" >&2; exit 1; }
else
    if oc get job download-model -n "$PROJECT" &>/dev/null; then
        echo "Download Job exists without its PVC; use a new project. No data was changed." >&2
        exit 1
    fi
    printf "➡️ Creating PVC for model storage...\n"
    cat <<EOF | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: model-pvc
  namespace: ${PROJECT}
  annotations:
    demo.vllm/model-id: ${MODEL_ID}
    demo.vllm/model-revision: ${MODEL_REVISION}
spec:
  storageClassName: ${STORAGE_CLASS}
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: ${PVC_SIZE}
EOF
fi
PVC_UID=$(oc get pvc model-pvc -n "$PROJECT" -o jsonpath='{.metadata.uid}')
[[ -n $PVC_UID ]] || { echo "Cannot establish PVC identity" >&2; exit 1; }

# --- Create HuggingFace secret (optional, for gated models) ---
if [ -n "$HF_TOKEN" ]; then
    printf "➡️ Creating HuggingFace token secret...\n"
    oc create secret generic huggingface-secret \
        -n "$PROJECT" \
        --from-literal=token="$HF_TOKEN" \
        --dry-run=client -o yaml | oc apply -f -
fi

# --- Download model via Job ---
if oc get job download-model -n "$PROJECT" &>/dev/null; then
    JOB_MODEL=$(oc get job download-model -n "$PROJECT" -o jsonpath='{.metadata.annotations.demo\.vllm/model-id}')
    JOB_REVISION=$(oc get job download-model -n "$PROJECT" -o jsonpath='{.metadata.annotations.demo\.vllm/model-revision}')
    JOB_PVC_UID=$(oc get job download-model -n "$PROJECT" -o jsonpath='{.metadata.annotations.demo\.vllm/pvc-uid}')
    if [[ $JOB_MODEL != "$MODEL_ID" || $JOB_REVISION != "$MODEL_REVISION" || $JOB_PVC_UID != "$PVC_UID" ]]; then
        echo "Job identity does not match this model revision and PVC; use a new project. Existing data was preserved." >&2
        exit 1
    fi
    JOB_STATUS=$(oc get job download-model -n "$PROJECT" -o jsonpath='{.status.succeeded}')
    if [ "$JOB_STATUS" = "1" ]; then
        printf "✅ Model ${GREEN}already downloaded${NC}\n"
        printf "\n✅ ${GREEN}Setup complete!${NC}\n"
        printf "   Next: Apply the ServingRuntime and InferenceService manifests\n\n"
        exit 0
    else
        printf "➡️ Existing download job found; waiting without interrupting it...\n"
        oc wait --for=condition=complete job/download-model -n "$PROJECT" --timeout="$DOWNLOAD_TIMEOUT"
        exit 0
    fi
fi

printf "➡️ Downloading model ${YELLOW}%s${NC} to PVC...\n" "$MODEL_ID"

# Build environment section based on whether HF_TOKEN is set
HF_ENV="- name: HF_HUB_DISABLE_XET
              value: \"1\""
if [ -n "$HF_TOKEN" ]; then
    HF_ENV="${HF_ENV}
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: huggingface-secret
                  key: token"
fi

cat <<EOF | oc apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: download-model
  namespace: ${PROJECT}
  annotations:
    demo.vllm/model-id: ${MODEL_ID}
    demo.vllm/model-revision: ${MODEL_REVISION}
    demo.vllm/pvc-uid: ${PVC_UID}
spec:
  template:
    spec:
      nodeSelector:
        ${STORAGE_KEY}: ${STORAGE_VALUE}
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      containers:
        - name: downloader
          image: ${DOWNLOAD_IMAGE}
          command:
            - bash
            - -c
            - |
              pip install -q huggingface_hub==0.34.4 && \
              python -c "
              from huggingface_hub import snapshot_download
              snapshot_download(
                  '${MODEL_ID}',
                  revision='${MODEL_REVISION}',
                  local_dir='/mnt/models',
              )
              print('Download complete!')
              "
          env:
            ${HF_ENV}
          volumeMounts:
            - name: model-storage
              mountPath: /mnt/models
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
            limits:
              cpu: "4"
              memory: "8Gi"
      volumes:
        - name: model-storage
          persistentVolumeClaim:
            claimName: model-pvc
      restartPolicy: Never
  backoffLimit: 2
EOF

printf "\n⏳ Waiting for model download to complete (this may take several minutes)...\n"
if ! oc wait --for=condition=complete job/download-model -n "$PROJECT" --timeout="$DOWNLOAD_TIMEOUT"; then
    oc logs job/download-model -n "$PROJECT" --tail=20 >&2 || true
    echo "Download not confirmed complete. The Job was preserved; rerun to resume waiting." >&2
    exit 1
fi

printf "\n✅ ${GREEN}Setup complete!${NC}\n"
printf "   Model downloaded to PVC ${YELLOW}model-pvc${NC}\n"
printf "   Next steps:\n"
printf "     1. oc apply -f 02-serving-runtime.yaml\n"
printf "     2. oc apply -f 03-inference-service.yaml\n"
printf "     3. ./04-run-inference.sh\n\n"
