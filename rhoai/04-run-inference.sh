#!/usr/bin/env bash
# Validate inference, collect benchmark evidence, or inspect metrics.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MODE=${1:-single}
case "$MODE" in single|benchmark|metrics|prefix) ;; *) echo "Usage: $0 [single|benchmark|metrics|prefix] [concurrency] [requests]" >&2; exit 2;; esac
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
MODEL_NAME=${MODEL_NAME:-qwen25-3b}
export MODEL_NAME

# An explicit URL supports existing endpoints without changing cluster context.
if [[ -z ${BASE_URL:-} ]]; then
    for dependency in oc curl; do command -v "$dependency" >/dev/null || { echo "$dependency is required" >&2; exit 1; }; done
    PROJECT=${PROJECT:-$(oc project -q)}
    INGRESS_PORT=${INGRESS_PORT:-8080}
    [[ $INGRESS_PORT =~ ^[0-9]+$ ]] && (( INGRESS_PORT > 0 && INGRESS_PORT <= 65535 )) || { echo "Invalid INGRESS_PORT" >&2; exit 2; }
    # Keep stdout as pure JSON so results can be piped to tools such as jq.
    oc wait -n "$PROJECT" --for=condition=Ready "inferenceservice/$MODEL_NAME" --timeout="${READY_TIMEOUT:-900s}" >&2
    POD=$(oc get pods -n "$PROJECT" -l "serving.kserve.io/inferenceservice=${MODEL_NAME}" -o json |
        python3 -c 'import json,sys; pods=json.load(sys.stdin)["items"]; ready=[p["metadata"]["name"] for p in pods if not p["metadata"].get("deletionTimestamp") and any(c["type"]=="Ready" and c["status"]=="True" for c in p.get("status",{}).get("conditions",[]))]; print(ready[0] if ready else "")')
    [[ -n $POD ]] || { echo "No Ready predictor Pod found" >&2; exit 1; }
    PF_LOG=$(mktemp)
    PF_PID=
    cleanup() { if [[ -n $PF_PID ]]; then kill "$PF_PID" 2>/dev/null || true; wait "$PF_PID" 2>/dev/null || true; fi; rm -f -- "$PF_LOG"; }
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    oc port-forward --address=127.0.0.1 -n "$PROJECT" "pod/${POD}" "${INGRESS_PORT}:8080" >"$PF_LOG" 2>&1 &
    PF_PID=$!
    BASE_URL="http://127.0.0.1:${INGRESS_PORT}"
    ready=false
    for ((attempt=0; attempt<60; attempt++)); do
        if ! kill -0 "$PF_PID" 2>/dev/null; then cat "$PF_LOG" >&2; exit 1; fi
        if grep -q "Forwarding from 127.0.0.1:${INGRESS_PORT} " "$PF_LOG" && curl --fail --silent --max-time 2 "$BASE_URL/health" >/dev/null; then ready=true; break; fi
        sleep 1
    done
    if [[ $ready != true ]]; then echo "Port-forward did not become healthy" >&2; cat "$PF_LOG" >&2; exit 1; fi
fi
export BASE_URL
python3 "$SCRIPT_DIR/inference_client.py" "$MODE" "${2:-5}" "${3:-10}"
