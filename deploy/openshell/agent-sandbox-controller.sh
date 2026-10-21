#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
CONFIG="$SCRIPT_DIR/../config/openshell.config.yaml"
CRD="sandboxes.agents.x-k8s.io"

usage() {
  echo "Usage: ./deploy/openshell/agent-sandbox-controller.sh            (install)"
  echo "       ./deploy/openshell/agent-sandbox-controller.sh cleanup    (remove)"
}

case "${1:-install}" in
  install)                      MODE="install" ;;
  cleanup|--cleanup|-d|delete)  MODE="cleanup" ;;
  *) echo -e "${RED}✗ unknown command: '$1'${NC}"; usage; exit 1 ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}✗ kubectl cannot reach a cluster. Set KUBECONFIG and retry.${NC}"; exit 1; }
[ -f "$CONFIG" ] || { echo -e "${RED}✗ config not found: $CONFIG${NC}"; exit 1; }

cfg() {
  grep -E "^[[:space:]]*$1:" "$CONFIG" | head -1 \
    | sed -E "s/^[^:]*:[[:space:]]*//; s/[[:space:]]*#.*$//; s/[[:space:]]*$//; s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/"
}

VER="$(cfg agent_sandbox_version)"; VER="${VER:-v0.5.1}"
MANIFEST="https://github.com/kubernetes-sigs/agent-sandbox/releases/download/$VER/manifest.yaml"

if [ "$MODE" = "cleanup" ]; then
  echo "🧹 agent-sandbox controller — cleanup ($VER)"
  echo -e "${YELLOW}  (delete any OpenShell releases first, or their sandbox CRs will be orphaned)${NC}"
  kubectl delete -f "$MANIFEST" --ignore-not-found || true
  echo -e "${GREEN}✓ agent-sandbox controller removed${NC}"
  exit 0
fi

echo "🧩 kubernetes-sigs agent-sandbox controller — install ($VER)"
echo "==========================================================="
if kubectl get crd "$CRD" >/dev/null 2>&1; then
  echo -e "${GREEN}✓ CRD $CRD already present — nothing to do${NC}"
  exit 0
fi
echo -e "${YELLOW}Applying $MANIFEST ...${NC}"
kubectl apply -f "$MANIFEST"
echo -e "${YELLOW}Waiting for CRD $CRD to register...${NC}"
kubectl wait --for=condition=Established "crd/$CRD" --timeout=60s
echo -e "${GREEN}✓ agent-sandbox controller installed.${NC}"
echo "   Next: ./deploy/vast-claw-deploy.sh   (full stack: platform + MCP tools)"
echo "   Or continue if this was invoked from vast-claw-deploy.sh (stage 1)."
