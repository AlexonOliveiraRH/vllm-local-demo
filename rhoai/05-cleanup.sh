#!/bin/bash
# Clean up RHOAI vLLM demo resources
# Usage:
#   ./05-cleanup.sh                  # delete all resources in the project
#   ./05-cleanup.sh my-project       # specify project name

PROJECT=${1:-vllm-demo}

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

printf "\n🗑️  ${CYAN}Cleaning up vLLM demo resources${NC}\n"
printf "   Project: ${YELLOW}%s${NC}\n\n" "$PROJECT"

read -p "Are you sure you want to delete project '$PROJECT'? (y/N) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Kill any port-forward processes
    pkill -f "port-forward.*qwen25-3b" 2>/dev/null && \
        printf "➡️ Stopped port-forward processes\n" || true

    printf "➡️ Deleting project ${YELLOW}%s${NC}...\n" "$PROJECT"
    oc delete project "$PROJECT" --wait=true

    printf "\n✅ ${GREEN}Cleanup complete!${NC}\n\n"
else
    printf "\n❌ Cleanup cancelled.\n\n"
fi
