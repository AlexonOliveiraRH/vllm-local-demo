#!/bin/bash
# Set up RHOAI project with model storage for vLLM inference
# Usage:
#   ./01-setup-project.sh                          # uses default project name
#   ./01-setup-project.sh my-project               # custom project name
#   HF_TOKEN=hf_xxx ./01-setup-project.sh          # with HuggingFace token (for gated models)

set -eo pipefail

PROJECT=${1:-vllm-demo}
MODEL_ID=${MODEL_ID:-Qwen/Qwen2.5-3B-Instruct}
PVC_SIZE=${PVC_SIZE:-10Gi}

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
    printf "✅ Project ${GREEN}%s${NC} already exists\n" "$PROJECT"
    oc project "$PROJECT"
else
    printf "➡️ Creating project ${YELLOW}%s${NC}...\n" "$PROJECT"
    oc new-project "$PROJECT"
fi

# Label namespace for RHOAI dashboard visibility
oc label namespace "$PROJECT" opendatahub.io/dashboard=true --overwrite

# --- Create PVC for model storage ---
if oc get pvc model-pvc -n "$PROJECT" &>/dev/null; then
    printf "✅ PVC ${GREEN}model-pvc${NC} already exists\n"
else
    printf "➡️ Creating PVC for model storage...\n"
    cat <<EOF | oc apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: model-pvc
  namespace: ${PROJECT}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: ${PVC_SIZE}
EOF
fi

# --- Create HuggingFace secret (optional, for gated models) ---
if [ -n "$HF_TOKEN" ]; then
    printf "➡️ Creating HuggingFace token secret...\n"
    oc create secret generic huggingface-secret \
        --from-literal=token="$HF_TOKEN" \
        --dry-run=client -o yaml | oc apply -f -
fi

# --- Download model via Job ---
if oc get job download-model -n "$PROJECT" &>/dev/null; then
    JOB_STATUS=$(oc get job download-model -n "$PROJECT" -o jsonpath='{.status.succeeded}')
    if [ "$JOB_STATUS" = "1" ]; then
        printf "✅ Model ${GREEN}already downloaded${NC}\n"
        printf "\n✅ ${GREEN}Setup complete!${NC}\n"
        printf "   Next: Apply the ServingRuntime and InferenceService manifests\n\n"
        exit 0
    else
        printf "➡️ Previous download job found, deleting and re-creating...\n"
        oc delete job download-model -n "$PROJECT" --wait=true
    fi
fi

printf "➡️ Downloading model ${YELLOW}%s${NC} to PVC...\n" "$MODEL_ID"

# Build environment section based on whether HF_TOKEN is set
HF_ENV="- name: HF_HUB_DISABLE_XET
              value: \"1\""
if [ -n "$HF_TOKEN" ]; then
    HF_ENV="${HF_ENV}
            - name: HUGGING_FACE_HUB_TOKEN
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
                  '${MODEL_ID}',
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
oc wait --for=condition=complete job/download-model -n "$PROJECT" --timeout=600s

printf "\n✅ ${GREEN}Setup complete!${NC}\n"
printf "   Model downloaded to PVC ${YELLOW}model-pvc${NC}\n"
printf "   Next steps:\n"
printf "     1. oc apply -f 02-serving-runtime.yaml\n"
printf "     2. oc apply -f 03-inference-service.yaml\n"
printf "     3. ./04-run-inference.sh\n\n"
