#!/usr/bin/env bash
# up: prepare and deploy; verify: new project, exercise all modes, then delete it.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ACTION=${1:-up}
PROJECT=${2:-vllm-demo}
export PROJECT
case "$ACTION" in up|verify) ;; *) echo "Usage: $0 [up|verify] [project]" >&2; exit 2;; esac
[[ $PROJECT =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#PROJECT} -le 63 ]] || { echo "Invalid project name" >&2; exit 2; }
if [[ -n ${BASE_URL:-} || -n ${RESULTS_FILE:-} ]]; then
    echo "BASE_URL and RESULTS_FILE are supported by 04-run-inference.sh, not lifecycle orchestration." >&2
    exit 2
fi
MODEL_NAME=qwen25-3b
export MODEL_NAME
for dependency in oc curl python3; do command -v "$dependency" >/dev/null; done
if [[ $ACTION == verify ]]; then
    # ProjectRequest is exclusive: AlreadyExists is an error, never adoption.
    EXPECTED_PROJECT_UID=$(python3 -c 'import json,os; print(json.dumps({"apiVersion":"project.openshift.io/v1","kind":"ProjectRequest","metadata":{"name":os.environ["PROJECT"]}}))' |
        oc create -f - -o json |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["metadata"]["uid"])')
    [[ $EXPECTED_PROJECT_UID =~ ^[a-f0-9-]+$ ]] || { echo "Project creation did not return a valid UID" >&2; exit 1; }
    export EXPECTED_PROJECT_UID
else
    unset EXPECTED_PROJECT_UID
fi
bash "$SCRIPT_DIR/01-setup-project.sh" "$PROJECT"
bash "$SCRIPT_DIR/02-deploy-runtime.sh" "$PROJECT"
bash "$SCRIPT_DIR/03-deploy-model.sh" "$PROJECT"
oc wait -n "$PROJECT" --for=condition=Ready inferenceservice/qwen25-3b --timeout="${READY_TIMEOUT:-1200s}"
if [[ $ACTION == verify ]]; then
    export MAX_TOKENS=${MAX_TOKENS:-32} IGNORE_EOS=${IGNORE_EOS:-1} STREAM=1
fi
bash "$SCRIPT_DIR/04-run-inference.sh" single
if [[ $ACTION == verify ]]; then
    for concurrency in 1 4 8; do
        bash "$SCRIPT_DIR/04-run-inference.sh" benchmark "$concurrency" 12
    done
    bash "$SCRIPT_DIR/04-run-inference.sh" metrics
    bash "$SCRIPT_DIR/04-run-inference.sh" prefix
    printf 'y\n' | bash "$SCRIPT_DIR/05-cleanup.sh" "$PROJECT"
    echo "Full lifecycle verification passed. The verification project was deleted."
else
    echo "Lab ready in project $PROJECT. Use 04-run-inference.sh for experiments."
fi
