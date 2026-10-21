#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/../config/openshell.config.yaml"
VALUES="$SCRIPT_DIR/values.yaml"
CRD="sandboxes.agents.x-k8s.io"

usage() {
  echo "Usage: ./deploy/openshell/vast-claw-openshell.sh            (deploy)"
  echo "       ./deploy/openshell/vast-claw-openshell.sh cleanup    (uninstall)"
}

case "${1:-deploy}" in
  deploy)                       MODE="deploy" ;;
  cleanup|--cleanup|-d|delete)  MODE="cleanup" ;;
  *) echo -e "${RED}✗ unknown command: '$1'${NC}"; usage; exit 1 ;;
esac

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }
command -v helm    >/dev/null 2>&1 || { echo -e "${RED}helm not found${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || {
  echo -e "${RED}✗ kubectl cannot reach a cluster. Set KUBECONFIG and retry.${NC}"; exit 1; }
[ -f "$CONFIG" ] || { echo -e "${RED}✗ config not found: $CONFIG${NC}"; exit 1; }
[ -f "$VALUES" ] || { echo -e "${RED}✗ values not found: $VALUES${NC}"; exit 1; }

cfg() {
  local v
  v="$(grep -E "^[[:space:]]*$1:" "$CONFIG" | head -1 \
    | sed -E "s/^[^:]*:[[:space:]]*//; s/[[:space:]]*#.*$//; s/[[:space:]]*$//; s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/")"
  printf '%s' "$v"
}

DEPTS="$(cfg departments)"
CHART_REPO="$(cfg chart_repository)"; CHART_NAME="$(cfg chart_name)"; CHART_VER="$(cfg chart_version)"
WS_SIZE="$(cfg workspace_storage_size)"; PULL_SECRET="$(cfg image_pull_secret)"
WS_SC="$(cfg workspace_storage_class)"; MAKE_DEFAULT="$(cfg make_storage_class_default)"
CHART_REPO="${CHART_REPO:-oci://ghcr.io/nvidia/openshell}"; CHART_NAME="${CHART_NAME:-helm-chart}"
WS_SIZE="${WS_SIZE:-10Gi}"; MAKE_DEFAULT="${MAKE_DEFAULT:-false}"
CHART_REF="$CHART_REPO/$CHART_NAME"
[ -n "$DEPTS" ] || { echo -e "${RED}✗ no departments set in $CONFIG${NC}"; exit 1; }

os_release() { echo "os-$1"; }

legacy_releases() { echo "openshell vastclaw-openshell-$1"; }

if [ "$MODE" = "cleanup" ]; then
  echo "🧹 OpenShell gateway — cleanup"
  OUR_NS=""
  for d in $DEPTS; do
    ns="vastclaw-$d"
    OUR_NS="$OUR_NS $ns"
    kubectl get ns "$ns" >/dev/null 2>&1 || continue

    for rel in "$(os_release "$d")" $(legacy_releases "$d"); do
      if helm -n "$ns" status "$rel" >/dev/null 2>&1; then
        echo -e "${YELLOW}  uninstalling $rel (ns $ns)...${NC}"
        helm -n "$ns" uninstall "$rel" --wait --timeout 180s >/dev/null 2>&1 \
          || helm -n "$ns" uninstall "$rel" >/dev/null 2>&1 || true
      fi
    done

    # Sandbox CRs outlive the gateway; delete them before the PVCs they mount.
    if kubectl get crd "$CRD" >/dev/null 2>&1; then
      kubectl -n "$ns" delete "$CRD" --all --ignore-not-found --timeout=120s >/dev/null 2>&1 || true
    fi

    for pvc in $(kubectl -n "$ns" get pvc -o name 2>/dev/null \
                   | grep -E '/openshell-data-' || true); do
      echo -e "${YELLOW}  deleting leftover $pvc (ns $ns)...${NC}"
      kubectl -n "$ns" delete "$pvc" --ignore-not-found --timeout=120s >/dev/null 2>&1 || true
    done
  done

  for kind in clusterrole clusterrolebinding; do
    while read -r name relns; do
      [ -n "$name" ] || continue
      case " $OUR_NS " in
        *" $relns "*)
          echo -e "${YELLOW}  deleting orphaned $kind/$name (was ns $relns)...${NC}"
          kubectl delete "$kind" "$name" --ignore-not-found >/dev/null 2>&1 || true
          ;;
      esac
    done < <(kubectl get "$kind" -l app.kubernetes.io/name=openshell -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.annotations.meta\.helm\.sh/release-namespace}{"\n"}{end}' 2>/dev/null)
  done

  echo -e "${GREEN}✓ gateways removed (namespaces kept; CRDs/controller left in place)${NC}"
  echo "  Remove the cluster-scoped controller with: ./deploy/openshell/agent-sandbox-controller.sh cleanup"
  exit 0
fi

echo "🐚 NVIDIA OpenShell gateway — install (per department)"
echo "======================================================"
echo "  Chart: $CHART_REF  version: ${CHART_VER:-<appVersion>}  departments: $DEPTS"
echo ""

# --- Gate: agent-sandbox controller must be installed (cluster-scoped, once) ---
if ! kubectl get crd "$CRD" >/dev/null 2>&1; then
  echo -e "${RED}✗ prerequisite missing: CRD $CRD${NC}"
  echo "  OpenShell needs the kubernetes-sigs agent-sandbox controller (cluster-scoped)."
  echo "  Install it once (cluster-admin), then re-run this script:"
  echo "     ./deploy/openshell/agent-sandbox-controller.sh"
  exit 1
fi
echo -e "${GREEN}✓ agent-sandbox controller present${NC}"

# --- Workspace StorageClass: the chart uses the cluster DEFAULT SC for all PVCs ---
if [ "$MAKE_DEFAULT" = "true" ] && [ -n "$WS_SC" ]; then
  echo -e "${YELLOW}Marking StorageClass '$WS_SC' as cluster default (workspace + gateway DB PVCs)...${NC}"
  kubectl get sc "$WS_SC" >/dev/null 2>&1 || {
    echo -e "${RED}✗ StorageClass '$WS_SC' not found — deploy the VAST CSI first${NC}"; exit 1; }
  # Unset default on any other class, then set it on ours (avoids two defaults).
  for sc in $(kubectl get sc -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{"\n"}{end}'); do
    [ "$sc" = "$WS_SC" ] && continue
    kubectl patch sc "$sc" -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' >/dev/null
  done
  kubectl patch sc "$WS_SC" -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}' >/dev/null
  echo -e "${GREEN}✓ default StorageClass = $WS_SC${NC}"
else
  DEF_SC="$(kubectl get sc -o jsonpath='{range .items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")]}{.metadata.name}{" "}{end}')"
  if [ -z "$DEF_SC" ]; then
    echo -e "${RED}✗ no default StorageClass on this cluster.${NC}"
    echo "  The OpenShell chart has no storageClassName, so its PVCs need a default SC."
    echo "  Either set make_storage_class_default: \"true\" (and workspace_storage_class) in"
    echo "  $CONFIG, or mark a VAST class default yourself, then re-run."
    exit 1
  fi
  echo -e "${GREEN}✓ cluster default StorageClass: $DEF_SC${NC} (sandbox + gateway DB PVCs land here)"
fi
echo ""

VER_FLAG=""; [ -n "$CHART_VER" ] && VER_FLAG="--version $CHART_VER"
for d in $DEPTS; do
  ns="vastclaw-$d"; rel="$(os_release "$d")"
  echo -e "${YELLOW}── department '$d' → release $rel (ns $ns) ──${NC}"
  kubectl create ns "$ns" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  SETS=(--set "server.sandboxNamespace=$ns" --set "server.workspaceDefaultStorageSize=$WS_SIZE"
        --set "fullnameOverride=$rel")
  [ -n "$PULL_SECRET" ] && SETS+=(--set "imagePullSecrets[0].name=$PULL_SECRET")
  # shellcheck disable=SC2086
  helm upgrade --install "$rel" "$CHART_REF" $VER_FLAG -n "$ns" -f "$VALUES" "${SETS[@]}" \
    --atomic --cleanup-on-fail --timeout 600s
  echo -e "${GREEN}✓ $rel deployed${NC}\n"
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
for d in $DEPTS; do
  ns="vastclaw-$d"
  echo "ns $ns:"; kubectl -n "$ns" get pods -l app.kubernetes.io/name=openshell -o wide 2>/dev/null || true
done
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✓ OpenShell gateway(s) ready.${NC}"
echo "  Gateway service: os-<dept>.<ns>.svc.cluster.local:8080 (plaintext e2e — see values.yaml)"
echo "  Backend spawn path: OpenShell CreateSandbox only (no direct-CR fallback)"
echo "  Next: rebuild/roll backend with openshell dep, then ./deploy/vast-claw-sanity.sh"
echo "🧹 Cleanup: ./deploy/openshell/vast-claw-openshell.sh cleanup"
