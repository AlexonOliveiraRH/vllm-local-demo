#!/bin/bash
# Run inferences against vLLM on RHOAI with GPU
# Usage:
#   ./04-run-inference.sh                       # single request
#   ./04-run-inference.sh benchmark             # benchmark (5 concurrent, 10 total)
#   ./04-run-inference.sh benchmark 10 50       # custom concurrency and total
#   ./04-run-inference.sh metrics               # show vLLM Prometheus metrics

MODE=${1:-single}
MODEL_NAME="qwen25-3b"
PROMPT="Being an IT professional is"

export INGRESS_HOST=${INGRESS_HOST:-localhost}
export INGRESS_PORT=${INGRESS_PORT:-8080}

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

printf "➡️ Using INGRESS_HOST=${INGRESS_HOST} and INGRESS_PORT=${INGRESS_PORT}\n"

# --- Discover predictor pod and set up port-forward ---
POD=$(oc get pod -l app=${MODEL_NAME}-predictor -o name 2>/dev/null | head -1)
if [ -z "$POD" ]; then
    POD=$(oc get pod -l serving.kserve.io/inferenceservice=${MODEL_NAME} -o name 2>/dev/null | head -1)
fi

if [ -z "$POD" ]; then
    printf "❌ No pod found for InferenceService ${YELLOW}%s${NC}. Is it deployed?\n" "$MODEL_NAME"
    exit 1
fi

PF_COMMAND_PATTERN="oc port-forward ${POD} ${INGRESS_PORT}:8080"

if pgrep -f "port-forward.*${MODEL_NAME}.*${INGRESS_PORT}" > /dev/null; then
    printf "✅ Port-forward process already detected. Proceeding.\n"
else
    printf "➡️ Starting port-forward to ${YELLOW}%s${NC}...\n" "$POD"
    ${PF_COMMAND_PATTERN} &
    PORT_FORWARD_PID=$!
    trap "echo '➡️ Stopping port-forward (PID: $PORT_FORWARD_PID)...'; kill $PORT_FORWARD_PID 2>/dev/null" EXIT
    sleep 2
fi

URL="http://${INGRESS_HOST}:${INGRESS_PORT}/v1/completions"

send_request() {
    local max_tokens=${1:-30}
    curl -s -w '\n{"_timing": {"ttfb": %{time_starttransfer}, "total": %{time_total}}}' \
        "${URL}" \
        -H "Content-Type: application/json" \
        -d "{
          \"model\": \"${MODEL_NAME}\",
          \"prompt\": \"${PROMPT}\",
          \"stream\": false,
          \"max_tokens\": ${max_tokens},
          \"stop\": [\".\"]
        }"
}

# --- Single request mode ---
if [ "$MODE" = "single" ]; then
    printf "\n🚀 Sending inference request: ${GREEN}\"${PROMPT}...\"${NC}\n"

    RESPONSE=$(send_request 30)
    ANSWER=$(echo "$RESPONSE" | head -1 | jq -r '.choices[0].text')
    TTFB=$(echo "$RESPONSE" | tail -1 | jq -r '._timing.ttfb')
    TOTAL=$(echo "$RESPONSE" | tail -1 | jq -r '._timing.total')
    TOKENS=$(echo "$RESPONSE" | head -1 | jq -r '.usage.completion_tokens // "N/A"')

    printf "\n🤖 Model Answer: ${GREEN}%s${NC}\n" "$ANSWER"
    printf "\n📊 ${CYAN}Performance:${NC}\n"
    printf "   ⏱️  Time to First Byte: ${YELLOW}%ss${NC}\n" "$TTFB"
    printf "   ⏱️  Total Time:         ${YELLOW}%ss${NC}\n" "$TOTAL"
    printf "   🔢 Tokens Generated:    ${YELLOW}%s${NC}\n" "$TOKENS"
    if [ "$TOKENS" != "N/A" ] && [ "$TOKENS" != "0" ]; then
        TPS=$(echo "scale=1; $TOKENS / $TOTAL" | bc 2>/dev/null || echo "N/A")
        printf "   ⚡ Tokens/sec:          ${YELLOW}%s${NC}\n" "$TPS"
    fi
    echo ""

# --- Benchmark mode ---
elif [ "$MODE" = "benchmark" ]; then
    CONCURRENCY=${2:-5}
    NUM_REQUESTS=${3:-10}

    printf "\n🏋️ ${CYAN}vLLM Benchmark (RHOAI GPU)${NC}\n"
    printf "   Concurrent requests: ${YELLOW}%s${NC}\n" "$CONCURRENCY"
    printf "   Total requests:      ${YELLOW}%s${NC}\n" "$NUM_REQUESTS"
    printf "   Model:               ${YELLOW}%s${NC}\n" "$MODEL_NAME"
    echo ""

    TMPDIR=$(mktemp -d)
    START_TIME=$(date +%s%N)

    seq 1 "$NUM_REQUESTS" | xargs -P "$CONCURRENCY" -I {} bash -c "
        RESP=\$(curl -s -w '%{time_starttransfer} %{time_total}' \
            '${URL}' \
            -H 'Content-Type: application/json' \
            -d '{
              \"model\": \"${MODEL_NAME}\",
              \"prompt\": \"${PROMPT}\",
              \"stream\": false,
              \"max_tokens\": 30,
              \"stop\": [\".\"]
            }' -o /dev/null)
        echo \"\$RESP\" > ${TMPDIR}/{}.txt
    "

    END_TIME=$(date +%s%N)
    WALL_TIME=$(echo "scale=2; ($END_TIME - $START_TIME) / 1000000000" | bc)

    TOTAL_TTFB=0
    TOTAL_LATENCY=0
    MIN_LATENCY=999999
    MAX_LATENCY=0
    COUNT=0

    for f in "${TMPDIR}"/*.txt; do
        TTFB=$(awk '{print $1}' "$f")
        LAT=$(awk '{print $2}' "$f")
        TOTAL_TTFB=$(echo "$TOTAL_TTFB + $TTFB" | bc)
        TOTAL_LATENCY=$(echo "$TOTAL_LATENCY + $LAT" | bc)
        if (( $(echo "$LAT < $MIN_LATENCY" | bc -l) )); then MIN_LATENCY=$LAT; fi
        if (( $(echo "$LAT > $MAX_LATENCY" | bc -l) )); then MAX_LATENCY=$LAT; fi
        COUNT=$((COUNT + 1))
    done

    AVG_TTFB=$(echo "scale=3; $TOTAL_TTFB / $COUNT" | bc)
    AVG_LATENCY=$(echo "scale=3; $TOTAL_LATENCY / $COUNT" | bc)
    THROUGHPUT=$(echo "scale=1; $COUNT / $WALL_TIME" | bc)

    printf "📊 ${CYAN}Results (${COUNT} requests):${NC}\n"
    printf "   ⏱️  Avg TTFB:           ${YELLOW}%ss${NC}\n" "$AVG_TTFB"
    printf "   ⏱️  Avg Latency:        ${YELLOW}%ss${NC}\n" "$AVG_LATENCY"
    printf "   ⏱️  Min/Max Latency:    ${YELLOW}%ss / %ss${NC}\n" "$MIN_LATENCY" "$MAX_LATENCY"
    printf "   🕐 Wall Clock Time:     ${YELLOW}%ss${NC}\n" "$WALL_TIME"
    printf "   🚀 Throughput:          ${YELLOW}%s req/s${NC}\n" "$THROUGHPUT"
    echo ""
    printf "   💡 ${GREEN}vLLM's continuous batching handles %s concurrent requests efficiently${NC}\n" "$CONCURRENCY"
    echo ""

    rm -rf "$TMPDIR"

# --- Metrics mode ---
elif [ "$MODE" = "metrics" ]; then
    printf "\n📊 ${CYAN}vLLM Prometheus Metrics${NC}\n\n"
    METRICS=$(curl -s "http://${INGRESS_HOST}:${INGRESS_PORT}/metrics")

    printf "${YELLOW}GPU Cache Usage:${NC}\n"
    echo "$METRICS" | grep "^vllm:gpu_cache_usage_perc" || echo "  (not available)"
    echo ""

    printf "${YELLOW}Request Queue:${NC}\n"
    echo "$METRICS" | grep "^vllm:num_requests_running\|^vllm:num_requests_waiting" || echo "  (not available)"
    echo ""

    printf "${YELLOW}Token Throughput:${NC}\n"
    echo "$METRICS" | grep "^vllm:prompt_tokens_total\|^vllm:generation_tokens_total" || echo "  (not available)"
    echo ""

    printf "${YELLOW}Prefix Cache:${NC}\n"
    echo "$METRICS" | grep "^vllm:prefix_cache" || echo "  (not available)"
    echo ""

else
    echo "Usage: $0 [single|benchmark|metrics] [concurrency] [num_requests]"
    echo "  single              - Single request with timing (default)"
    echo "  benchmark [C] [N]   - N requests with C concurrency (default: 5 concurrent, 10 total)"
    echo "  metrics             - Show vLLM Prometheus metrics snapshot"
fi
