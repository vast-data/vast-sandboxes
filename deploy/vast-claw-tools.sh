#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

case "${1:-deploy}" in
  deploy)                 MODE="deploy" ;;
  cleanup|--cleanup|-d|delete) MODE="cleanup" ;;
  *) echo -e "${RED}✗ unknown command: '$1'${NC}"
     echo "Usage: ./deploy/vast-claw-tools.sh            (deploy all MCP tools)"
     echo "       ./deploy/vast-claw-tools.sh cleanup    (remove all MCP tools)"
     exit 1 ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}✗ kubectl cannot reach a cluster. Set KUBECONFIG and retry.${NC}"; exit 1; }

shopt -s nullglob
PLATFORM="${PLATFORM_CONFIG:-$SCRIPT_DIR/config/workflow-ui.config.yaml}"
CONFIGS=("$SCRIPT_DIR"/config/mcp-*.config.yaml)
[ ${#CONFIGS[@]} -gt 0 ] || { echo -e "${RED}✗ no deploy/config/mcp-*.config.yaml found${NC}"; exit 1; }
if [ "$MODE" != "cleanup" ]; then
  [ -f "$PLATFORM" ] || { echo -e "${RED}✗ config not found: $PLATFORM${NC}"; exit 1; }
  VAST_ACCESS="$(vc_yaml_block_value "$PLATFORM" vast access_key)"
  VAST_SECRET="$(vc_yaml_block_value "$PLATFORM" vast secret_key)"
  VDB_ENDPOINT="$(vc_yaml_block_value "$PLATFORM" vdb endpoint)"
  S3_ENDPOINT="$(vc_yaml_block_value "$PLATFORM" s3 endpoint)"
  MCP_IMAGE="$(vc_yaml_block_value "$PLATFORM" runtime mcp_image)"
  [ -n "$VAST_ACCESS" ] && [ -n "$VAST_SECRET" ] && [ -n "$VDB_ENDPOINT" ] && [ -n "$S3_ENDPOINT" ] && [ -n "$MCP_IMAGE" ] || {
    echo -e "${RED}✗ vast keys, vdb.endpoint, s3.endpoint, and runtime.mcp_image are required in $PLATFORM${NC}"; exit 1; }
fi

for CONFIG in "${CONFIGS[@]}"; do
  base="$(basename "$CONFIG")"; DEPT="${base#mcp-}"; DEPT="${DEPT%.config.yaml}"
  if [ -n "${DEPT_FILTER:-}" ] && [ "$DEPT" != "$DEPT_FILTER" ]; then
    continue
  fi
  NS="vastclaw-$DEPT"; DEPLOY="vastclaw-mcp-$DEPT"; SECRET="vastclaw-mcp-$DEPT-config"
  MANIFEST="$SCRIPT_DIR/k8s/40-mcp-$DEPT.yaml"
  [ -f "$MANIFEST" ] || { echo -e "${RED}✗ manifest not found for $DEPT: $MANIFEST${NC}"; exit 1; }

  if [ "$MODE" = "cleanup" ]; then
    echo -e "${YELLOW}Removing MCP tools: $DEPT (namespace kept)...${NC}"
    kubectl delete -f "$MANIFEST" --ignore-not-found
    kubectl -n "$NS" delete secret "$SECRET" --ignore-not-found
  else
    echo -e "${YELLOW}Deploying MCP tools: $DEPT...${NC}"
    RENDERED_CONFIG="$(mktemp)"
    RENDERED_MANIFEST="$(mktemp)"
    sed -e "s|__VAST_ACCESS_KEY__|$VAST_ACCESS|g" \
        -e "s|__VAST_SECRET_KEY__|$VAST_SECRET|g" \
        -e "s|__VAST_VDB_ENDPOINT__|$VDB_ENDPOINT|g" \
        -e "s|__VAST_S3_ENDPOINT__|$S3_ENDPOINT|g" \
        "$CONFIG" > "$RENDERED_CONFIG"
    sed -e "s|__VC_MCP_IMAGE__|$MCP_IMAGE|g" "$MANIFEST" > "$RENDERED_MANIFEST"
    if grep -qE '__VAST_|__VC_' "$RENDERED_CONFIG" "$RENDERED_MANIFEST"; then
      rm -f "$RENDERED_CONFIG" "$RENDERED_MANIFEST"
      echo -e "${RED}✗ unresolved markers in MCP config for $DEPT${NC}"; exit 1
    fi
    kubectl -n "$NS" create secret generic "$SECRET" \
      --from-file=config.yaml="$RENDERED_CONFIG" \
      --dry-run=client -o yaml | kubectl apply -f -
    rm -f "$RENDERED_CONFIG"
    kubectl apply -f "$RENDERED_MANIFEST"
    rm -f "$RENDERED_MANIFEST"
    kubectl -n "$NS" rollout restart "deploy/$DEPLOY" >/dev/null 2>&1 || true
  fi
done

if [ "$MODE" = "cleanup" ]; then
  echo -e "${GREEN}✓ all MCP tools removed${NC}"
else
  echo -e "${GREEN}✓ all MCP tools deployed${NC}"
  echo "🧪 Smoke (per dept), e.g. sales — RAG over call_chunks, no NIM:"
  echo "   kubectl -n vastclaw-sales exec -i deploy/vastclaw-mcp-sales -- python - <<'PY'"
  echo "   from vastclaw_mcp.config import Settings"
  echo "   from vastclaw_mcp.identity import AgentIdentity"
  echo "   from vastclaw_mcp.rag_search import RagSearch"
  echo "   s=Settings.from_yaml(); i=AgentIdentity.from_env()"
  echo "   h=RagSearch(s).transcript_search(i,[0.1]*s.vdb.embedding_dim,limit=5); print('rows:',len(h))"
  echo "   [print(' ',x.engagement_id,x.speaker_type,round(x.distance,3),x.text[:60]) for x in h]"
  echo "   PY"
fi
