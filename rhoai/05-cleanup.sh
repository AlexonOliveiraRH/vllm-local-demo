#!/bin/bash
# Clean up RHOAI vLLM demo resources
# Usage:
#   ./05-cleanup.sh                  # delete all resources in the project
#   ./05-cleanup.sh my-project       # specify project name
set -euo pipefail

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
    # Each inference invocation owns and stops its own port-forward process.
    # Never kill unrelated forwarding sessions or infer a different project.
    printf "➡️ Deleting project ${YELLOW}%s${NC}...\n" "$PROJECT"
    if [[ -n ${EXPECTED_PROJECT_UID:-} ]]; then
        # A UID precondition makes deletion safe even if the name was recreated.
        export EXPECTED_PROJECT_UID
        python3 -c 'import json,os; print(json.dumps({"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":os.environ["EXPECTED_PROJECT_UID"]}}))' |
            oc delete --raw "/api/v1/namespaces/${PROJECT}" -f - >/dev/null
        oc wait --for=delete "namespace/${PROJECT}" --timeout=600s
    else
        oc delete project "$PROJECT" --wait=true
    fi

    printf "\n✅ ${GREEN}Cleanup complete!${NC}\n\n"
else
    printf "\n❌ Cleanup cancelled.\n\n"
fi
