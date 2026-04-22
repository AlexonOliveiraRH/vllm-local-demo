#!/bin/bash
# Benchmark llama.cpp server (OpenAI-compatible API)
# Usage:
#   ./llamacpp-bench.sh              # single request
#   ./llamacpp-bench.sh benchmark    # benchmark with concurrent requests

MODE=${1:-single}
MODEL_NAME="gemma-3-270m"
PROMPT="Being an IT professional is"

HOST=${LLAMA_HOST:-localhost}
PORT=${LLAMA_PORT:-8080}

URL="http://${HOST}:${PORT}/v1/completions"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PROPS=$(curl -s "http://${HOST}:${PORT}/props" 2>/dev/null)
N_SLOTS=$(echo "$PROPS" | jq -r '.total_slots // empty' 2>/dev/null)
N_SLOTS=${N_SLOTS:-"?"}

MODEL_PATH=$(echo "$PROPS" | jq -r '.model_path // empty' 2>/dev/null)
if [ -n "$MODEL_PATH" ]; then
    MODEL_DISPLAY=$(basename "$MODEL_PATH" .gguf)
else
    MODEL_DISPLAY="${MODEL_NAME}"
fi

LLAMA_BACKEND=${LLAMA_BACKEND:-"auto"}
if [ "$LLAMA_BACKEND" = "auto" ]; then
    if curl -s "http://${HOST}:${PORT}/health" &>/dev/null; then
        BACKEND_LABEL="llama.cpp server (n_parallel=${N_SLOTS})"
    else
        BACKEND_LABEL="llama.cpp server"
    fi
else
    BACKEND_LABEL="llama.cpp server (${LLAMA_BACKEND}, n_parallel=${N_SLOTS})"
fi

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
    printf "\n🚀 Sending inference request to llama.cpp: ${GREEN}\"${PROMPT}...\"${NC}\n"
    printf "   Server: ${CYAN}${HOST}:${PORT}${NC}\n\n"

    RESPONSE=$(send_request 30)
    ANSWER=$(echo "$RESPONSE" | head -1 | jq -r '.choices[0].text')
    TTFB=$(echo "$RESPONSE" | tail -1 | jq -r '._timing.ttfb')
    TOTAL=$(echo "$RESPONSE" | tail -1 | jq -r '._timing.total')
    TOKENS=$(echo "$RESPONSE" | head -1 | jq -r '.usage.completion_tokens // "N/A"')

    printf "🤖 Model Answer: ${GREEN}%s${NC}\n" "$ANSWER"
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

    printf "\n🏋️ ${CYAN}llama.cpp Benchmark${NC}\n"
    printf "   Server:              ${YELLOW}${HOST}:${PORT}${NC}\n"
    printf "   Concurrent requests: ${YELLOW}%s${NC}\n" "$CONCURRENCY"
    printf "   Total requests:      ${YELLOW}%s${NC}\n" "$NUM_REQUESTS"
    printf "   Model:               ${YELLOW}%s${NC}\n" "$MODEL_DISPLAY"
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
    printf "   💡 ${GREEN}%s, bench concurrency=%s${NC}\n" "$BACKEND_LABEL" "$CONCURRENCY"
    echo ""

    rm -rf "$TMPDIR"
else
    echo "Usage: $0 [single|benchmark] [concurrency] [num_requests]"
    echo "  single              - Single request with timing (default)"
    echo "  benchmark [C] [N]   - N requests with C concurrency (default: 5 concurrent, 10 total)"
fi
