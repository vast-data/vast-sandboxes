#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$SCRIPT_DIR/vast-block-csi.config.yaml"
REPO_URL="https://vast-data.github.io/vast-csi"
PROVISIONER="block.csi.vastdata.com"
SECRET="vast-secret"

usage() {
  echo "Usage: ./deploy/csi/vast-block-csi.sh            (deploy)"
  echo "       ./deploy/csi/vast-block-csi.sh cleanup    (uninstall)"
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
[ -f "$CONFIG" ] || {
  echo -e "${RED}✗ config not found: $CONFIG${NC}"
  echo "  cp deploy/config/vast-block-csi.config.example.yaml deploy/config/vast-block-csi.config.yaml"
  exit 1
}

cfg() {
  local v
  v="$(grep -E "^[[:space:]]*$1:" "$CONFIG" | head -1 \
    | sed -E "s/^[^:]*:[[:space:]]*//; s/[[:space:]]*#.*$//; s/[[:space:]]*$//; s/^\"(.*)\"$/\1/; s/^'(.*)'$/\1/")"
  [ "$v" = "REPLACE_ME" ] && v=""
  printf '%s' "$v"
}

NS="$(cfg namespace)";        RELEASE="$(cfg release)";      CHART_VER="$(cfg chart_version)"
ENDPOINT="$(cfg endpoint)";   USER="$(cfg username)";        PASS="$(cfg password)"
VERIFY_SSL="$(cfg verify_ssl)"
SC="$(cfg name)";             VIP="$(cfg vip_pool_name)";    VIP_FQDN="$(cfg vip_pool_fqdn)"
SUBSYS="$(cfg subsystem)";    TENANT="$(cfg tenant_name)"
RECLAIM="$(cfg reclaim_policy)"; MAKE_DEFAULT="$(cfg make_default)"
NS="${NS:-vastcsi}"; RELEASE="${RELEASE:-vastblock}"; SC="${SC:-vastdata-block}"
VERIFY_SSL="${VERIFY_SSL:-false}"; RECLAIM="${RECLAIM:-Delete}"; MAKE_DEFAULT="${MAKE_DEFAULT:-false}"
# SC vip params want a bare FQDN/pool name — strip any scheme + trailing slash.
strip_scheme() { local v="${1#http://}"; v="${v#https://}"; printf '%s' "${v%/}"; }
VIP="$(strip_scheme "$VIP")"; VIP_FQDN="$(strip_scheme "$VIP_FQDN")"

if [ "$MODE" = "cleanup" ]; then
  echo "🧹 VAST Block CSI — Cleanup"
  echo "  (delete PVCs using $SC first, or their volumes will be orphaned on VAST)"
  kubectl delete volumesnapshotclass "$SC-snapshot" --ignore-not-found 2>/dev/null || true
  kubectl delete storageclass "$SC" --ignore-not-found
  helm -n "$NS" uninstall "$RELEASE" 2>/dev/null || true
  kubectl -n "$NS" delete secret "$SECRET" --ignore-not-found
  echo -e "${GREEN}✓ block CSI removed (namespace $NS kept; delete manually if unused)${NC}"
  exit 0
fi

echo "🧱 VAST Block CSI (NVMe/TCP) — install"
echo "======================================"
missing=""
for kv in "vms.endpoint=$ENDPOINT" "vms.username=$USER" "vms.password=$PASS" \
          "storageclass.subsystem=$SUBSYS" "storageclass.tenant_name=$TENANT"; do
  [ -n "${kv#*=}" ] || missing="$missing ${kv%%=*}"
done
[ -n "$VIP$VIP_FQDN" ] || missing="$missing storageclass.vip_pool_name|vip_pool_fqdn"
if [ -n "$missing" ]; then
  echo -e "${RED}✗ missing required config:${NC}$missing"
  echo "  Fill $CONFIG and retry."; exit 1
fi
echo "  Namespace: $NS   Release: $RELEASE   SC: $SC   subsystem: $SUBSYS"
[ -n "$VIP_FQDN" ] && echo "  vip_pool_fqdn: $VIP_FQDN" || echo "  vip_pool_name: $VIP"
[ -n "$TENANT" ] && echo "  tenant: $TENANT"
echo ""

echo -e "${YELLOW}Step 1/6: helm repo...${NC}"
helm repo add vastcsi-repo "$REPO_URL" >/dev/null 2>&1 || true
helm repo update vastcsi-repo >/dev/null
echo -e "${GREEN}✓ repo ready${NC}\n"

echo -e "${YELLOW}Step 2/6: namespace + secret ($SECRET)...${NC}"
kubectl create ns "$NS" --dry-run=client -o yaml | kubectl apply -f -
SECRET_ARGS=(
  --from-literal=username="$USER"
  --from-literal=password="$PASS"
  --from-literal=endpoint="$ENDPOINT"
)
[ -n "$TENANT" ] && SECRET_ARGS+=(--from-literal=tenant="$TENANT")
kubectl -n "$NS" create secret generic "$SECRET" \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo -e "${GREEN}✓ secret applied${NC}\n"

echo -e "${YELLOW}Step 3/6: install Kubernetes external-snapshotter (VolumeSnapshot API)...${NC}"
SNAP_VER="${SNAP_VER:-7.0.1}"
SNAP_BASE="https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/v${SNAP_VER}"
if kubectl get crd volumesnapshots.snapshot.storage.k8s.io >/dev/null 2>&1; then
  echo -e "${GREEN}✓ snapshot CRDs already present${NC}\n"
else
  for crd in \
    snapshot.storage.k8s.io_volumesnapshotclasses.yaml \
    snapshot.storage.k8s.io_volumesnapshotcontents.yaml \
    snapshot.storage.k8s.io_volumesnapshots.yaml; do
    kubectl apply -f "${SNAP_BASE}/client/config/crd/${crd}"
  done
  kubectl apply -f "${SNAP_BASE}/deploy/kubernetes/snapshot-controller/rbac-snapshot-controller.yaml"
  kubectl apply -f "${SNAP_BASE}/deploy/kubernetes/snapshot-controller/setup-snapshot-controller.yaml"
  kubectl -n kube-system rollout status deploy/snapshot-controller --timeout=180s || true
  echo -e "${GREEN}✓ external-snapshotter v${SNAP_VER} installed${NC}\n"
fi

echo -e "${YELLOW}Step 4/6: install block chart ($RELEASE)...${NC}"
VALUES="$(mktemp)"
cat > "$VALUES" <<YAML
secretName: $SECRET
endpoint: "$ENDPOINT"
verifySsl: $VERIFY_SSL
YAML
VER_FLAG=""; [ -n "$CHART_VER" ] && VER_FLAG="--version $CHART_VER"
# shellcheck disable=SC2086
helm upgrade -n "$NS" --install "$RELEASE" vastcsi-repo/vastblock -f "$VALUES" $VER_FLAG
rm -f "$VALUES"
kubectl -n "$NS" wait --for=condition=Ready pods --all --timeout=180s || true
echo -e "${GREEN}✓ chart installed${NC}\n"

echo -e "${YELLOW}Step 5/6: StorageClass $SC (provisioner $PROVISIONER)...${NC}"
DEFAULT_ANN=""
[ "$MAKE_DEFAULT" = "true" ] && DEFAULT_ANN=$'\n    storageclass.kubernetes.io/is-default-class: "true"'
# vip_pool_fqdn takes precedence over vip_pool_name when set.
if [ -n "$VIP_FQDN" ]; then VIP_PARAM="vip_pool_fqdn: $VIP_FQDN"; else VIP_PARAM="vip_pool_name: $VIP"; fi
TENANT_PARAM=""
[ -n "$TENANT" ] && TENANT_PARAM=$'\n  tenant_name: '"$TENANT"
cat <<YAML | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: $SC
  labels:
    app.kubernetes.io/part-of: vastclaw
  annotations: {}$DEFAULT_ANN
provisioner: $PROVISIONER
parameters:
  subsystem: $SUBSYS
  $VIP_PARAM$TENANT_PARAM
  csi.storage.k8s.io/fstype: ext4
  volume_name_fmt: "csi:{namespace}:{name}:{id}"
  eph_volume_name_fmt: "csi:{namespace}:{name}:{id}"
  csi.storage.k8s.io/provisioner-secret-name: $SECRET
  csi.storage.k8s.io/provisioner-secret-namespace: $NS
  csi.storage.k8s.io/controller-publish-secret-name: $SECRET
  csi.storage.k8s.io/controller-publish-secret-namespace: $NS
  csi.storage.k8s.io/node-publish-secret-name: $SECRET
  csi.storage.k8s.io/node-publish-secret-namespace: $NS
  csi.storage.k8s.io/node-expand-secret-name: $SECRET
  csi.storage.k8s.io/node-expand-secret-namespace: $NS
  csi.storage.k8s.io/controller-expand-secret-name: $SECRET
  csi.storage.k8s.io/controller-expand-secret-namespace: $NS
reclaimPolicy: $RECLAIM
volumeBindingMode: Immediate
allowVolumeExpansion: true
YAML
echo -e "${GREEN}✓ StorageClass applied${NC}\n"

echo -e "${YELLOW}Step 6/6: VolumeSnapshotClass $SC-snapshot (agent clone primitive)...${NC}"
if kubectl apply -f - <<YAML 2>/dev/null
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshotClass
metadata:
  name: $SC-snapshot
  labels:
    app.kubernetes.io/part-of: vastclaw
driver: $PROVISIONER
deletionPolicy: Delete
YAML
then
  echo -e "${GREEN}✓ VolumeSnapshotClass applied${NC}\n"
else
  echo -e "${YELLOW}⚠ snapshot CRDs not installed — skipped (install external-snapshotter to enable clones)${NC}\n"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
kubectl -n "$NS" get pods -o wide
echo "SC provisioner: $(kubectl get sc "$SC" -o jsonpath='{.provisioner}' 2>/dev/null)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}✓ block CSI ready.${NC} The agent runtime provisions from it:"
echo "  deploy/config/workflow-ui.config.yaml -> sandbox.storage_class: $SC  (access_mode: ReadWriteOnce)"
echo "  clone-on-spawn + checkpoint: sandbox.clone_subagents_from_head / checkpoint_before_fanout"
echo ""
echo "🧪 Repeatable proof: ./deploy/vast-claw-snapshot-smoke.sh"
echo ""
echo "🔎 If a PVC stays Pending, the events tell the truth:"
echo "   kubectl describe pvc <name> | sed -n '/Events/,\$p'"
echo "   (403 tenants -> set storageclass.tenant_name)"
echo "🧹 Cleanup: ./deploy/csi/vast-block-csi.sh cleanup"
