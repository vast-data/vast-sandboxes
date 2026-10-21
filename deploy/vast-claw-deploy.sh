#!/bin/bash

set -euo pipefail

# Resolve paths relative to this script so it runs from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

NS="vastclaw-observability"
SECRET="vastclaw-workflow-ui-config"
TLS_SECRET_NAME="vastclaw-tls"
BASE_DOMAIN="${BASE_DOMAIN:-}"
TLS_CERT_FILE="${TLS_CERT_FILE:-}"
TLS_KEY_FILE="${TLS_KEY_FILE:-}"
UI_MANIFEST="$SCRIPT_DIR/k8s/30-workflow-ui.yaml"
CONFIG="$SCRIPT_DIR/config/workflow-ui.config.yaml"
SETTLE_SECONDS="${SETTLE_SECONDS:-30}"
CONTROLLER_SH="$SCRIPT_DIR/openshell/agent-sandbox-controller.sh"
TOOLS_SH="$SCRIPT_DIR/vast-claw-tools.sh"

apply_ui_manifest() {
  local backend frontend
  backend="$(vc_yaml_block_value "$CONFIG" runtime agent_image)"
  frontend="$(vc_yaml_block_value "$CONFIG" runtime frontend_image)"
  [ -n "$backend" ] && [ -n "$frontend" ] || {
    echo -e "${RED}✗ runtime.agent_image and runtime.frontend_image are required in $CONFIG${NC}"; exit 1; }
  sed -e "s|TLS_SECRET_NAME|$TLS_SECRET_NAME|g" \
      -e "s|__VC_BACKEND_IMAGE__|$backend|g" \
      -e "s|__VC_FRONTEND_IMAGE__|$frontend|g" \
      "$UI_MANIFEST" | kubectl apply -f -
}

delete_ui_manifest() {
  sed -e "s/TLS_SECRET_NAME/$TLS_SECRET_NAME/g" "$UI_MANIFEST" \
    | kubectl delete -f - --ignore-not-found
}

settle() {
  local why="${1:-next stage}"
  echo -e "${YELLOW}⏳ settling ${SETTLE_SECONDS}s (${why})…${NC}"
  sleep "$SETTLE_SECONDS"
}

usage() {
  echo "Usage: ./deploy/vast-claw-deploy.sh [CONFIG]   (full deploy)"
  echo "       ./deploy/vast-claw-deploy.sh cleanup    (keep corpus tables)"
  echo "       ./deploy/vast-claw-deploy.sh cleanup-all (drop corpus tables too)"
  echo "  SETTLE_SECONDS=${SETTLE_SECONDS} between deploy stages (override via env)."
}

# Fail fast on bad usage before touching the cluster.
case "${1:-}" in
  "" | cleanup|--cleanup|-d|delete|cleanup-all|--cleanup-all) : ;;
  clean|cleanups|clear|delete-all|destroy|teardown|rm|del|--clean|uninstall)
    echo -e "${RED}✗ '$1' is not a command. Use 'cleanup' or 'cleanup-all'.${NC}"; usage; exit 1 ;;
  -*) echo -e "${RED}✗ unknown flag: '$1'${NC}"; usage; exit 1 ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}✗ kubectl cannot reach a cluster. Set KUBECONFIG and retry.${NC}"; exit 1; }

case "${1:-}" in
  cleanup|--cleanup|-d|delete|cleanup-all|--cleanup-all)
    if [ "$1" = "cleanup-all" ] || [ "$1" = "--cleanup-all" ]; then
      DROP_ACTION="drop"
      REMOVE_CMD="remove"
      echo "🧹 vast-claw — cleanup all (including corpus tables)"
    else
      DROP_ACTION="drop-keep-corpus"
      REMOVE_CMD="remove-keep-corpus"
      echo "🧹 vast-claw — cleanup (corpus tables kept)"
    fi
    echo "=========================="
    if [ -x "$SCRIPT_DIR/vast-claw-sanity.sh" ]; then
      echo -e "${YELLOW}Stopping local sanity script (if running)...${NC}"
      "$SCRIPT_DIR/vast-claw-sanity.sh" stop 2>/dev/null || true
    fi
    if [ -f "$CONFIG" ]; then
      echo -e "${YELLOW}Dropping VastDB schemas and tables…${NC}"
      DROP_CONFIG="$(mktemp)"
      kubectl apply -f "$SCRIPT_DIR/k8s/00-namespaces.yaml" >/dev/null
      if python3 "$SCRIPT_DIR/lib/vast_platform.py" render "$CONFIG" >"$DROP_CONFIG" \
        && "$SCRIPT_DIR/lib/run_vast_tables.sh" "$DROP_CONFIG" "$DROP_ACTION"; then
        echo -e "${GREEN}✓ VastDB table drop finished${NC}\n"
      else
        echo -e "${YELLOW}⚠ VastDB table drop failed — continuing teardown${NC}\n"
      fi
      rm -f "$DROP_CONFIG"
    fi
    echo -e "${YELLOW}Wiping vastclaw-db + agent sandboxes/PVCs (via backend pod, before teardown)...${NC}"
    if "$SCRIPT_DIR/vast-claw-db-cleanup.sh"; then
      echo -e "${GREEN}✓ platform data + agent storage wiped${NC}\n"
    else
      echo -e "${YELLOW}⚠ platform cleanup had errors — continuing k8s teardown${NC}\n"
    fi
    echo -e "${YELLOW}Removing MCP tools...${NC}"
    "$TOOLS_SH" cleanup 2>/dev/null || true
    echo -e "${YELLOW}Deleting workflow UI (deployments/services/ingress)...${NC}"
    delete_ui_manifest
    kubectl -n "$NS" delete secret "$SECRET" --ignore-not-found
    kubectl -n "$NS" delete secret "$TLS_SECRET_NAME" --ignore-not-found
    echo -e "${YELLOW}Deleting RBAC (cluster-scoped reaper is NOT cascaded by ns delete)...${NC}"
    kubectl delete -f "$SCRIPT_DIR/k8s/20-rbac.yaml" --ignore-not-found
    echo -e "${YELLOW}Deleting NemoClaw blueprint ConfigMaps...${NC}"
    "$SCRIPT_DIR/nemoclaw/vast-claw-nemoclaw.sh" cleanup 2>/dev/null || true
    echo -e "${YELLOW}Uninstalling OpenShell gateways...${NC}"
    if ! "$SCRIPT_DIR/openshell/vast-claw-openshell.sh" cleanup; then
      echo -e "${YELLOW}⚠ OpenShell cleanup failed — re-run it before the next deploy, or"
      echo -e "  leftover PVCs / ClusterRoles will block the install${NC}"
    fi
    echo -e "${YELLOW}Deleting namespaces (cascades any remaining objects, incl. tools)...${NC}"
    kubectl delete -f "$SCRIPT_DIR/k8s/00-namespaces.yaml" --ignore-not-found
    echo -e "${YELLOW}Removing Agent-Sandbox controller + CRD...${NC}"
    if [ -x "$CONTROLLER_SH" ]; then
      "$CONTROLLER_SH" cleanup 2>/dev/null || true
    else
      echo -e "${YELLOW}⚠ missing $CONTROLLER_SH — skip${NC}"
    fi
    VAST_REMOVAL_OK=0
    if [ -f "$CONFIG" ]; then
      echo -e "${YELLOW}Removing VAST buckets and UI logins from ${CONFIG}…${NC}"
      if python3 "$SCRIPT_DIR/lib/vast_platform.py" "$REMOVE_CMD" "$CONFIG"; then
        echo -e "${GREEN}✓ VAST object removal finished${NC}"
        VAST_REMOVAL_OK=1
      else
        echo -e "${YELLOW}⚠ VAST object removal failed — tenant, view policy, and role were not touched${NC}"
      fi
    else
      echo -e "${YELLOW}⚠ $CONFIG missing — skipped VAST object removal${NC}"
    fi
    echo "  (block CSI left installed — ./deploy/csi/vast-block-csi.sh cleanup if needed)"
    if [ "$VAST_REMOVAL_OK" != 1 ]; then
      echo -e "${RED}✗ cleanup finished the cluster teardown, but the VAST buckets were not removed${NC}"
      exit 1
    fi
    if [ "$REMOVE_CMD" = "remove" ]; then
      echo -e "${GREEN}✓ cleanup-all complete (corpus tables and buckets removed)${NC}"
    else
      echo -e "${GREEN}✓ cleanup complete (corpus tables and bucket kept)${NC}"
    fi
    exit 0 ;;
esac

if [ -n "${1:-}" ]; then
  if [ -f "$1" ]; then
    CONFIG="$1"
  else
    echo -e "${RED}Error: '$1' is not a config file. Deploy does not take a cluster name.${NC}"
    usage
    exit 1
  fi
fi

HOST="${INGRESS_HOST:-vastclaw.solutions.vastdata.com}"

echo "🚀 vast-claw — Full deploy"
echo "========================="
echo "  HTTPS: $HOST"

echo -e "${YELLOW}Models (chat + embed must already be serving)…${NC}"
if ! python3 "$SCRIPT_DIR/lib/check_models.py" "$CONFIG"; then
  echo -e "${RED}✗ model check failed. Start the chat and embed servers, then re-run deploy.${NC}"
  exit 1
fi
echo ""

# --- Stage 1/3: Agent-Sandbox controller (CRD) ---
echo -e "${YELLOW}Stage 1/3: Agent-Sandbox controller…${NC}"
[ -x "$CONTROLLER_SH" ] || {
  echo -e "${RED}✗ missing $CONTROLLER_SH${NC}"; exit 1; }
"$CONTROLLER_SH" || {
  echo -e "${RED}✗ agent-sandbox controller install failed${NC}"; exit 1; }
settle "before platform deploy"

echo -e "${YELLOW}Checking prerequisites...${NC}"
[ -f "$CONFIG" ] || {
  echo -e "${RED}✗ config not found: $CONFIG${NC}"
  echo "  cp deploy/config/workflow-ui.config.example.yaml deploy/config/workflow-ui.config.yaml"
  echo "  then fill the copy."; exit 1; }
if grep -q "CHANGE_ME_LONG_RANDOM" "$CONFIG"; then
  echo -e "${RED}✗ jwt_secret is still CHANGE_ME_LONG_RANDOM in $CONFIG${NC}"
  echo "  Set it, e.g.: openssl rand -hex 32"; exit 1; fi
# Certificate paths: env wins, otherwise tls.cert_file / tls.key_file in the config.
cfg_val() {
  grep -E "^[[:space:]]*$1:" "$CONFIG" | head -1 \
    | sed -E "s/^[^:]*:[[:space:]]*//; s/[[:space:]]*#.*$//; s/[[:space:]]*$//; s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/"
}
[ -n "$TLS_CERT_FILE" ] || TLS_CERT_FILE="$(cfg_val cert_file)"
[ -n "$TLS_KEY_FILE" ] || TLS_KEY_FILE="$(cfg_val key_file)"
if [ -z "$TLS_CERT_FILE" ] || [ -z "$TLS_KEY_FILE" ]; then
  echo -e "${RED}✗ TLS certificate path is not set${NC}"
  echo "  Set tls.cert_file and tls.key_file in $CONFIG"
  echo "  or export TLS_CERT_FILE and TLS_KEY_FILE."; exit 1
fi
for f in "$TLS_CERT_FILE" "$TLS_KEY_FILE"; do
  [ -f "$f" ] || {
    echo -e "${RED}✗ TLS file not found: $f${NC}"
    echo "  Set tls.cert_file and tls.key_file in $CONFIG."; exit 1; }
done

# Namespaces must exist before in-cluster data-plane probes (pod needs a ns).
echo -e "${YELLOW}Step 1/7: namespaces (early — needed for in-cluster preflight)…${NC}"
kubectl apply -f "$SCRIPT_DIR/k8s/00-namespaces.yaml"
echo -e "${GREEN}✓ namespaces ready${NC}\n"

echo -e "${YELLOW}  VAST tenant, users, and buckets…${NC}"
if ! python3 "$SCRIPT_DIR/lib/vast_platform.py" ensure "$CONFIG"; then
  echo -e "${RED}✗ VAST object bootstrap failed.${NC}"
  exit 1
fi
echo ""
MERGED="$(mktemp)"
TABLE_MARK="$(mktemp)"
trap 'rm -f "$MERGED" "$TABLE_MARK"' EXIT
python3 "$SCRIPT_DIR/lib/vast_platform.py" render "$CONFIG" >"$MERGED"
if ! "$SCRIPT_DIR/lib/run_vast_tables.sh" "$MERGED" ensure "$TABLE_MARK"; then
  echo -e "${RED}✗ VastDB schema bootstrap failed.${NC}"
  exit 1
fi
echo ""
INGEST="$(tr -d '[:space:]' <"$TABLE_MARK" 2>/dev/null || true)"
if [ "$INGEST" = "skip" ]; then
  echo -e "${GREEN}✓ corpus table already has rows — ingest skipped${NC}\n"
elif ! "$SCRIPT_DIR/lib/run_corpus_load.sh" "$MERGED"; then
  echo -e "${RED}✗ corpus load failed.${NC}"
  exit 1
fi
echo ""

echo -e "${YELLOW}  Preflight (config + k8s + in-cluster VAST buckets/endpoints)…${NC}"
if ! "$SCRIPT_DIR/lib/check_prereqs.sh" "$MERGED"; then
  echo -e "${RED}✗ fix the failed checks above, then re-run deploy.${NC}"
  exit 1
fi
echo -e "${GREEN}✓ prerequisites OK${NC}\n"

# --- Stage 2/3: platform ---
echo -e "${YELLOW}Stage 2/3: platform (RBAC → UI)…${NC}"

echo -e "${YELLOW}Step 2/7: RBAC (PVCs, Agent-Sandbox Sandboxes, VolumeSnapshots, jobs)...${NC}"
kubectl apply -f "$SCRIPT_DIR/k8s/20-rbac.yaml"
echo -e "${GREEN}✓ RBAC applied${NC}\n"

echo -e "${YELLOW}Step 3/7: config secret ($SECRET)...${NC}"
# Rendered file: generated keys included, VMS admin password removed.
kubectl -n "$NS" create secret generic "$SECRET" \
  --from-file=config.yaml="$MERGED" \
  --dry-run=client -o yaml | kubectl apply -f -
echo -e "${GREEN}✓ secret applied${NC}\n"

echo -e "${YELLOW}Step 4/7: TLS secret ($TLS_SECRET_NAME)...${NC}"
kubectl -n "$NS" delete secret "$TLS_SECRET_NAME" --ignore-not-found >/dev/null 2>&1
kubectl -n "$NS" create secret tls "$TLS_SECRET_NAME" \
  --cert="$TLS_CERT_FILE" \
  --key="$TLS_KEY_FILE"
echo -e "${GREEN}✓ TLS secret applied${NC}\n"

echo -e "${YELLOW}Step 5/7: NemoClaw blueprint + OpenShell policy (per department)...${NC}"
if "$SCRIPT_DIR/nemoclaw/vast-claw-nemoclaw.sh" apply; then
  echo -e "${GREEN}✓ NemoClaw blueprint applied${NC}\n"
else
  echo -e "${RED}✗ NemoClaw blueprint FAILED to apply (see error above).${NC}"
  echo -e "${RED}  Sub-agents need configmap/vastclaw-nemoclaw-blueprint (per-dept policy).${NC}"
  echo -e "${RED}  Fix the error, then re-run: ./deploy/nemoclaw/vast-claw-nemoclaw.sh apply${NC}\n"
  exit 1
fi

echo -e "${YELLOW}Step 6/7: OpenShell gateways (per department)...${NC}"
# Required — OpenShell is the only sub-agent spawn path. CRD installed in stage 1.
if ! kubectl get crd sandboxes.agents.x-k8s.io >/dev/null 2>&1; then
  echo -e "${RED}✗ CRD sandboxes.agents.x-k8s.io still missing after stage 1${NC}"; exit 1
fi
if "$SCRIPT_DIR/openshell/vast-claw-openshell.sh"; then
  echo -e "${GREEN}✓ OpenShell gateways applied${NC}\n"
else
  echo -e "${RED}✗ OpenShell gateway deploy FAILED (see error above).${NC}"
  echo -e "${RED}  Sub-agent spawn goes through the gateway only — fix and re-run:${NC}"
  echo -e "${RED}    ./deploy/openshell/vast-claw-openshell.sh${NC}\n"
  exit 1
fi

echo -e "${YELLOW}Step 7/7: workflow UI (backend + frontend + HTTPS ingress)...${NC}"
kubectl -n "$NS" delete ingress vastclaw-workflow-ui-frontend-tls --ignore-not-found >/dev/null 2>&1 || true
apply_ui_manifest
kubectl -n "$NS" rollout restart deploy/vastclaw-workflow-ui-backend >/dev/null 2>&1 || true
if ! kubectl -n "$NS" get secret "$TLS_SECRET_NAME" >/dev/null 2>&1; then
  echo -e "${RED}✗ TLS secret $TLS_SECRET_NAME missing in $NS${NC}"; exit 1
fi
ING_TLS_HOST=$(kubectl -n "$NS" get ingress vastclaw-workflow-ui-frontend \
  -o jsonpath='{.spec.tls[0].hosts[0]}' 2>/dev/null || echo "")
ING_SECRET=$(kubectl -n "$NS" get ingress vastclaw-workflow-ui-frontend \
  -o jsonpath='{.spec.tls[0].secretName}' 2>/dev/null || echo "")
if [ "$ING_TLS_HOST" != "$HOST" ] || [ "$ING_SECRET" != "$TLS_SECRET_NAME" ]; then
  echo -e "${RED}✗ ingress TLS misconfigured (host=$ING_TLS_HOST secret=$ING_SECRET)${NC}"; exit 1
fi
echo -e "${GREEN}✓ workflow UI applied (TLS host=$HOST secret=$TLS_SECRET_NAME)${NC}\n"

settle "before MCP tools"

# --- Stage 3/3: MCP tools ---
echo -e "${YELLOW}Stage 3/3: MCP tools (per department)…${NC}"
[ -x "$TOOLS_SH" ] || {
  echo -e "${RED}✗ missing $TOOLS_SH${NC}"; exit 1; }
PLATFORM_CONFIG="$MERGED" "$TOOLS_SH" || {
  echo -e "${RED}✗ MCP tools deploy failed${NC}"; exit 1; }
echo -e "${GREEN}✓ MCP tools applied${NC}\n"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
kubectl -n "$NS" get pods,svc,ingress
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
ING_IP=$(kubectl -n "$NS" get ingress vastclaw-workflow-ui-frontend \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
[ -z "$ING_IP" ] && ING_IP=$(kubectl -n "$NS" get ingress vastclaw-workflow-ui-frontend \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
if [ -n "$ING_IP" ]; then
  echo -e "Add to /etc/hosts:  ${YELLOW}$ING_IP  $HOST${NC}"
else
  echo -e "${YELLOW}⏳ Ingress address pending — check: kubectl -n $NS get ingress${NC}"
fi
echo -e "🌐 Open  ${GREEN}https://$HOST${NC}"
echo "🔒 TLS certificate: $TLS_CERT_FILE"
echo ""
echo "🔍 Logs:"
echo "  Backend:  kubectl -n $NS logs -f deploy/vastclaw-workflow-ui-backend"
echo "  Frontend: kubectl -n $NS logs -f deploy/vastclaw-workflow-ui-frontend"
echo ""
echo -e "${GREEN}✓ full deploy complete${NC}"
echo "🧹 Cleanup: ./deploy/vast-claw-deploy.sh cleanup       (keeps corpus tables)"
echo "🧹 Cleanup: ./deploy/vast-claw-deploy.sh cleanup-all   (drops corpus tables)"
echo "🧪 Sanity:  ./deploy/vast-claw-sanity.sh"
