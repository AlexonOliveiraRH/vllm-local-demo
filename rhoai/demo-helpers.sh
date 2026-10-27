#!/usr/bin/env bash
# Interactive helpers for exploring the lab. Load them into your current Bash shell:
#   source ./demo-helpers.sh
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    echo "Load these helpers into your shell with: source ./demo-helpers.sh" >&2
    exit 2
fi
command -v jq >/dev/null || echo "demo-helpers: jq is not installed; the summary_* filters need it." >&2

# Report a failure when any command in a pipeline fails, not only the last one.
set -o pipefail

show_deployment() {
    local project=${PROJECT:?Set PROJECT to the lab project first} model=${MODEL_NAME:-qwen25-3b}
    oc get inferenceservice "$model" -n "$project" \
        -o 'custom-columns=NAME:.metadata.name,READY:.status.conditions[?(@.type=="Ready")].status,RUNTIME:.spec.predictor.model.runtime,MODEL:.spec.predictor.model.storageUri' || return
    echo
    oc get pvc model-pvc -n "$project" \
        -o 'custom-columns=NAME:.metadata.name,STATUS:.status.phase,CAPACITY:.status.capacity.storage' || return
    echo
    oc get pods -n "$project" -l "serving.kserve.io/inferenceservice=$model" \
        -o 'custom-columns=PHASE:.status.phase,READY:.status.containerStatuses[*].ready,GPU_LIMIT:.spec.containers[*].resources.limits.nvidia\.com/gpu'
}

summary_single() { jq -c '{ok: "\(.summary.successful_requests)/\(.summary.attempted_requests)", tokens: .summary.output_tokens, latency_s: (.summary.latency_seconds.mean*100|round/100), tok_per_s: (.summary.output_tokens_per_second*10|round/10)}'; }
summary_bench() { jq -c '{c: .configuration.concurrency, ok: "\(.summary.successful_requests)/\(.summary.attempted_requests)", tok_per_s: (.summary.output_tokens_per_second*10|round/10), e2e_s: (.summary.latency_seconds.mean*100|round/100), ttft_s: (.summary.ttft_seconds.mean*100|round/100), tpot_ms: (.summary.tpot_seconds.mean*1000|round)}'; }
summary_prefix() { jq -c '{reuse: .cache_reuse_observed, prompt_tokens: .warm.prompt_tokens, cached_tokens: (.prefix_counter_delta.hits|floor), ttft_cold_s: (.cold.ttft_seconds*100|round/100), ttft_warm_s: (.warm.ttft_seconds*100|round/100), tpot_cold_ms: (.cold.tpot_seconds*1000|round), tpot_warm_ms: (.warm.tpot_seconds*1000|round)}'; }

echo "demo-helpers: loaded show_deployment, summary_single, summary_bench, summary_prefix (pipefail on)" >&2
