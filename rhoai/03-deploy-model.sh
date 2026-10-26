#!/usr/bin/env bash
# Apply the model with the same resolved placement used for its download PVC.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=${1:-vllm-demo}
[[ $PROJECT =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#PROJECT} -le 63 ]] || { echo "Invalid project name" >&2; exit 2; }
LOCAL_MODEL=$(oc create --dry-run=client -n "$PROJECT" -f - -o json < "$SCRIPT_DIR/03-inference-service.yaml")
MANIFEST=$(printf '%s\n' "$LOCAL_MODEL" |
    python3 "$SCRIPT_DIR/placement.py" render "$PROJECT")
printf '%s\n' "$MANIFEST" | oc apply -n "$PROJECT" -f -
