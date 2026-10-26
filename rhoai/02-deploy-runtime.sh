#!/usr/bin/env bash
# Deploy the local runtime configuration with the installed RHOAI image.
# Both the manual workflow and 00-run-lab.sh use this entry point.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT=${1:-vllm-demo}
[[ $PROJECT =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#PROJECT} -le 63 ]] || { echo "Invalid project name" >&2; exit 2; }
for dependency in oc python3; do command -v "$dependency" >/dev/null; done

# Internal descriptive metadata, never an override or proof of package version.
RESOLVED_RUNTIME_VERSION=
if [[ -z ${VLLM_IMAGE:-} ]]; then
    RUNTIME_SELECTION=$(oc get template vllm-cuda-runtime-template -n redhat-ods-applications -o json |
        python3 -c '
import json, sys
template = json.load(sys.stdin)
matches = [(obj, container.get("image", ""))
           for obj in template.get("objects", []) if obj.get("kind") == "ServingRuntime"
           for container in obj.get("spec", {}).get("containers", [])
           if container.get("name") == "kserve-container"]
if len(matches) != 1:
    sys.exit("Expected exactly one kserve-container image in the installed template; set VLLM_IMAGE explicitly")
runtime, image = matches[0]
if (not isinstance(image, str) or not image or "\x00" in image
        or any(c.isspace() for c in image)):
    sys.exit("Installed runtime image must be a nonempty string without whitespace or NUL")
annotations = runtime.get("metadata", {}).get("annotations") or {}
version = ""
if "opendatahub.io/runtime-version" in annotations:
    version = annotations["opendatahub.io/runtime-version"]
    if (not isinstance(version, str) or not version or "\x00" in version or any(c.isspace() for c in version)
            or any(c in version for c in "${}<>")):
        sys.exit("Installed runtime-version must be a nonempty string without whitespace, NUL, or unresolved template tokens")
print(image, version)')
    read -r VLLM_IMAGE RESOLVED_RUNTIME_VERSION <<< "$RUNTIME_SELECTION"
fi
export VLLM_IMAGE RESOLVED_RUNTIME_VERSION
# Validate and materialize the whole manifest before any apply. Never edit YAML
# in place, assume container order, or fall back silently to the example digest.
LOCAL_RUNTIME=$(oc create --dry-run=client -n "$PROJECT" -f - -o json < "$SCRIPT_DIR/02-serving-runtime.yaml")
RUNTIME_JSON=$(printf '%s\n' "$LOCAL_RUNTIME" |
    python3 -c '
import json, os, sys
image = os.environ["VLLM_IMAGE"]
if not image or any(c.isspace() for c in image):
    sys.exit("Runtime image must be nonempty and contain no whitespace")
if any(c in image for c in "${}<>"):
    sys.exit("Runtime image contains unresolved template tokens")
runtime = json.load(sys.stdin)
containers = [c for c in runtime["spec"]["containers"] if c.get("name") == "kserve-container"]
if len(containers) != 1:
    sys.exit("Expected exactly one kserve-container in the local runtime manifest")
containers[0]["image"] = image
annotations = runtime["metadata"].get("annotations", {})
annotations.pop("opendatahub.io/runtime-version", None)
if os.environ["RESOLVED_RUNTIME_VERSION"]:
    runtime["metadata"].setdefault("annotations", {})["opendatahub.io/runtime-version"] = os.environ["RESOLVED_RUNTIME_VERSION"]
print(json.dumps(runtime))')
printf 'Runtime image: %s\n' "$VLLM_IMAGE" >&2
printf '%s\n' "$RUNTIME_JSON" | oc apply -n "$PROJECT" -f -
