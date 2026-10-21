#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
source "$SCRIPT_DIR/../lib/common.sh"
CM_NAME="vastclaw-nemoclaw-blueprint"
DEPARTMENTS="${DEPARTMENTS:-sales finance}"

case "${1:-apply}" in
  apply)                        MODE="apply" ;;
  cleanup|--cleanup|-d|delete)  MODE="cleanup" ;;
  *) echo -e "${RED}✗ unknown command: '${1:-}' (use apply|cleanup)${NC}"; exit 1 ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}✗ kubectl cannot reach a cluster. Set KUBECONFIG and retry.${NC}"; exit 1; }

if [ "$MODE" = "cleanup" ]; then
  echo "🧹 NemoClaw blueprint — cleanup"
  for DEPT in $DEPARTMENTS; do
    kubectl -n "vastclaw-$DEPT" delete configmap "$CM_NAME" --ignore-not-found
  done
  echo -e "${GREEN}✓ blueprint ConfigMaps removed${NC}"
  exit 0
fi

echo "🐾 NemoClaw blueprint — apply (per department)"
echo "=============================================="
echo "  departments: $DEPARTMENTS"

CONFIG="${CONFIG:-$SCRIPT_DIR/../config/workflow-ui.config.yaml}"
[ -f "$CONFIG" ] || { echo -e "${RED}✗ config not found: $CONFIG${NC}"; exit 1; }

model_url() {
  awk -v k="$1:" '
    /^[A-Za-z_]/ { inmodel = ($0 ~ /^model:/) }
    inmodel && $1 == k { v=$2; gsub(/"/,"",v); print v; exit }
  ' "$CONFIG"
}

# "http(s)://host[:port]/path" -> "host port" (default port from scheme).
host_port() {
  local url="$1" scheme rest host port
  url="${url%\"}"; url="${url#\"}"
  scheme="${url%%://*}"; rest="${url#*://}"; rest="${rest%%/*}"
  case "$rest" in
    *:*) host="${rest%%:*}"; port="${rest##*:}" ;;
    *)   host="$rest"; [ "$scheme" = "https" ] && port=443 || port=80 ;;
  esac
  printf '%s %s' "$host" "$port"
}

CHAT_URL="$(model_url base_url)"
EMBED_URL="$(model_url embed_base_url)"
[ -n "$CHAT_URL" ]  || { echo -e "${RED}✗ model.base_url not found in $CONFIG${NC}"; exit 1; }
[ -n "$EMBED_URL" ] || EMBED_URL="$CHAT_URL"   # empty embed => reuse generation endpoint
read -r CHAT_HOST CHAT_PORT   <<EOF
$(host_port "$CHAT_URL")
EOF
read -r EMBED_HOST EMBED_PORT <<EOF
$(host_port "$EMBED_URL")
EOF
[ -n "$CHAT_HOST" ] && [ -n "$CHAT_PORT" ] && [ -n "$EMBED_HOST" ] && [ -n "$EMBED_PORT" ] || {
  echo -e "${RED}✗ could not parse NIM host:port (chat='$CHAT_URL' embed='$EMBED_URL')${NC}"; exit 1; }
case "$CHAT_HOST$EMBED_HOST" in
  *CHANGE_ME*) echo -e "${RED}✗ model endpoints still contain CHANGE_ME in $CONFIG — set them first${NC}"; exit 1 ;;
esac
echo "  NIM endpoints (rendered from $CONFIG):"
echo "    chat  → ${CHAT_HOST}:${CHAT_PORT}"
echo "    embed → ${EMBED_HOST}:${EMBED_PORT}"

AGENT_IMAGE="$(vc_yaml_block_value "$CONFIG" runtime agent_image)"
S3_HOST="$(vc_url_host "$(vc_yaml_block_value "$CONFIG" s3 endpoint)")"
VDB_HOST="$(vc_url_host "$(vc_yaml_block_value "$CONFIG" vdb endpoint)")"
VMS_HOST="$(vc_url_host "$(vc_yaml_block_value "$CONFIG" auth vms_host)")"
[ -n "$AGENT_IMAGE" ] && [ -n "$S3_HOST" ] && [ -n "$VDB_HOST" ] && [ -n "$VMS_HOST" ] || {
  echo -e "${RED}✗ runtime.agent_image, s3.endpoint, vdb.endpoint, and auth.vms_host are required in $CONFIG${NC}"; exit 1; }
case "$AGENT_IMAGE$S3_HOST$VDB_HOST$VMS_HOST" in
  *CHANGE_ME*|*REPLACE_ME*) echo -e "${RED}✗ image or VAST hosts still contain placeholders in $CONFIG${NC}"; exit 1 ;;
esac
echo "  images and VAST hosts (rendered from $CONFIG):"
echo "    agent → $AGENT_IMAGE"
echo "    s3    → $S3_HOST"
echo "    vdb   → $VDB_HOST"
echo "    vms   → $VMS_HOST"

for DEPT in $DEPARTMENTS; do
  NS="vastclaw-$DEPT"
  DEPT_DIR="$SCRIPT_DIR/departments/$DEPT"
  BLUEPRINT="$DEPT_DIR/blueprint.yaml"
  POLICY="$DEPT_DIR/policies/sandbox-policy.yaml"
  echo -e "${YELLOW}── $NS → configmap/$CM_NAME ──${NC}"

  [ -f "$BLUEPRINT" ] || { echo -e "${RED}✗ not found: $BLUEPRINT${NC}"; exit 1; }
  [ -f "$POLICY" ]    || { echo -e "${RED}✗ not found: $POLICY${NC}"; exit 1; }

  echo "  ensuring namespace $NS ..."
  kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

  echo "  rendering policy endpoints + applying configmap (source: departments/$DEPT/) ..."
  RENDERED_POLICY="$(mktemp)"
  RENDERED_BLUEPRINT="$(mktemp)"
  sed -e "s|__NIM_CHAT_HOST__|$CHAT_HOST|g" \
      -e "s|__NIM_CHAT_PORT__|$CHAT_PORT|g" \
      -e "s|__NIM_EMBED_HOST__|$EMBED_HOST|g" \
      -e "s|__NIM_EMBED_PORT__|$EMBED_PORT|g" \
      -e "s|__VAST_S3_HOST__|$S3_HOST|g" \
      -e "s|__VAST_VDB_HOST__|$VDB_HOST|g" \
      -e "s|__VAST_VMS_HOST__|$VMS_HOST|g" \
      "$POLICY" > "$RENDERED_POLICY"
  sed -e "s|__VC_BACKEND_IMAGE__|$AGENT_IMAGE|g" "$BLUEPRINT" > "$RENDERED_BLUEPRINT"
  if grep -qE '__NIM_|__VAST_|__VC_' "$RENDERED_POLICY" "$RENDERED_BLUEPRINT"; then
    rm -f "$RENDERED_POLICY" "$RENDERED_BLUEPRINT"
    echo -e "${RED}✗ unresolved markers in rendered blueprint for $DEPT${NC}"; exit 1
  fi
  manifest="$(kubectl -n "$NS" create configmap "$CM_NAME" \
    --from-file=blueprint.yaml="$RENDERED_BLUEPRINT" \
    --from-file=sandbox-policy.yaml="$RENDERED_POLICY" \
    --dry-run=client -o yaml)"
  rm -f "$RENDERED_POLICY" "$RENDERED_BLUEPRINT"
  printf '%s\n' "$manifest" | kubectl apply -f -

  kubectl -n "$NS" get configmap "$CM_NAME" >/dev/null
  echo -e "${GREEN}✓ $NS${NC}"
done

echo -e "${GREEN}✓ NemoClaw blueprint published to all departments.${NC}"
echo "🧹 Cleanup: ./deploy/nemoclaw/vast-claw-nemoclaw.sh cleanup"
