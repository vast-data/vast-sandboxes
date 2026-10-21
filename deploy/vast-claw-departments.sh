#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

PACKS_DIR="$SCRIPT_DIR/departments"

usage() {
  echo "Usage: $0 list"
  echo "       $0 apply <department|all>"
  echo ""
  echo "Examples:"
  echo "  $0 apply sales      # namespace + RBAC + MCP + NemoClaw for sales"
  echo "  $0 apply all        # every pack under deploy/departments/"
}

list_packs() {
  local d
  for d in "$PACKS_DIR"/*/pack.yaml; do
    [ -f "$d" ] || continue
    echo "  $(basename "$(dirname "$d")")"
  done
}

apply_pack() {
  local dept="$1"
  local pack="$PACKS_DIR/$dept/pack.yaml"
  [ -f "$pack" ] || { echo -e "${RED}✗ no pack at $pack${NC}"; exit 1; }

  echo -e "${CYAN}▸${NC} applying department pack: ${YELLOW}$dept${NC}"

  echo -e "${YELLOW}  namespaces + RBAC (from deploy/k8s/)...${NC}"
  kubectl apply -f "$SCRIPT_DIR/k8s/00-namespaces.yaml"
  kubectl apply -f "$SCRIPT_DIR/k8s/20-rbac.yaml"

  echo -e "${YELLOW}  MCP tools...${NC}"
  DEPT_FILTER="$dept" "$SCRIPT_DIR/vast-claw-tools.sh" deploy

  echo -e "${YELLOW}  NemoClaw blueprint...${NC}"
  DEPARTMENTS="$dept" "$SCRIPT_DIR/nemoclaw/vast-claw-nemoclaw.sh" apply

  echo -e "${GREEN}✓ department '$dept' applied${NC}"
  echo "  Next: ensure workflow-ui.config.yaml has auth user + departments.$dept block,"
  echo "        configure deploy/config/mcp-${dept}.config.yaml,"
  echo "        then redeploy vc-backend + vc-mcp image if code changed."
}

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}✗ kubectl cannot reach a cluster. Set KUBECONFIG and retry.${NC}"; exit 1; }

case "${1:-}" in
  list)
    echo "Department packs:"
    list_packs
    ;;
  apply)
    target="${2:-}"
    [ -n "$target" ] || { usage; exit 1; }
    if [ "$target" = "all" ]; then
      while IFS= read -r dept; do
        [ -n "$dept" ] && apply_pack "$dept"
      done < <(list_packs)
    else
      apply_pack "$target"
    fi
    ;;
  *)
    usage
    exit 1
    ;;
esac
