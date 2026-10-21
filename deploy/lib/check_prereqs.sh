#!/usr/bin/env bash
# Deploy preflight:
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
CONFIG="${1:-}"
PREFLIGHT_NS="${PREFLIGHT_NS:-vastclaw-observability}"
FAILS=0

fail() { echo -e "${RED}✗ $*${NC}" >&2; FAILS=$((FAILS + 1)); }
ok()   { echo -e "${GREEN}✓ $*${NC}"; }

if [ -z "$CONFIG" ] || [ ! -f "$CONFIG" ]; then
  echo -e "${RED}✗ usage: $0 <workflow-ui.config.yaml>${NC}" >&2
  exit 2
fi

command -v python3 >/dev/null 2>&1 || { fail "python3 required"; exit 1; }
command -v kubectl >/dev/null 2>&1 || { fail "kubectl required"; exit 1; }

echo -e "${YELLOW}▸ config (local)…${NC}"
PY_OUT="$(mktemp)"
set +e
python3 "$SCRIPT_DIR/check_prereqs.py" config "$CONFIG" >"$PY_OUT" 2>&1
PY_RC=$?
set -e
grep -v '^PREFLIGHT_' "$PY_OUT" || true
STORAGE_CLASS="$(grep '^PREFLIGHT_STORAGE_CLASS=' "$PY_OUT" | cut -d= -f2- || true)"
RUNTIME_MODE="$(grep '^PREFLIGHT_RUNTIME_MODE=' "$PY_OUT" | cut -d= -f2- || true)"
AGENT_IMAGE="$(grep '^PREFLIGHT_AGENT_IMAGE=' "$PY_OUT" | cut -d= -f2- || true)"
rm -f "$PY_OUT"
if [ "$PY_RC" -ne 0 ]; then
  exit 1
fi

STORAGE_CLASS="${STORAGE_CLASS:-vastdata-block}"
RUNTIME_MODE="${RUNTIME_MODE:-kubernetes}"
AGENT_IMAGE="${PREFLIGHT_IMAGE:-$AGENT_IMAGE}"
if [ -z "$AGENT_IMAGE" ]; then
  fail "no runtime.agent_image / PREFLIGHT_IMAGE for in-cluster probe"
  exit 1
fi

echo ""
echo -e "${YELLOW}▸ Kubernetes cluster…${NC}"

if kubectl get storageclass "$STORAGE_CLASS" >/dev/null 2>&1; then
  PROV="$(kubectl get storageclass "$STORAGE_CLASS" -o jsonpath='{.provisioner}' 2>/dev/null || true)"
  case "$PROV" in
    *block*)
      ok "StorageClass $STORAGE_CLASS (provisioner=$PROV)"
      ;;
    *)
      fail "StorageClass $STORAGE_CLASS provisioner='$PROV' — expect block.csi.vastdata.com"
      ;;
  esac
else
  fail "StorageClass '$STORAGE_CLASS' missing — install block CSI: ./deploy/csi/vast-block-csi.sh"
fi

SNAP_CLASS="${STORAGE_CLASS}-snapshot"
if kubectl get volumesnapshotclass "$SNAP_CLASS" >/dev/null 2>&1; then
  ok "VolumeSnapshotClass $SNAP_CLASS"
elif [ "$RUNTIME_MODE" = "kubernetes" ]; then
  fail "VolumeSnapshotClass '$SNAP_CLASS' missing — re-run ./deploy/csi/vast-block-csi.sh"
fi

if kubectl get crd volumesnapshots.snapshot.storage.k8s.io >/dev/null 2>&1; then
  ok "VolumeSnapshot CRD present"
elif [ "$RUNTIME_MODE" = "kubernetes" ]; then
  fail "VolumeSnapshot CRD missing — install external-snapshotter"
fi

if kubectl get crd sandboxes.agents.x-k8s.io >/dev/null 2>&1; then
  ok "Agent-Sandbox CRD sandboxes.agents.x-k8s.io"
elif [ "$RUNTIME_MODE" = "kubernetes" ]; then
  fail "CRD sandboxes.agents.x-k8s.io missing — run agent-sandbox-controller.sh"
fi

if kubectl -n vastcsi get secret vast-mgmt >/dev/null 2>&1; then
  ok "CSI mgmt secret vastcsi/vast-mgmt"
elif kubectl get ns vast-block-csi >/dev/null 2>&1; then
  ok "vast-block-csi namespace present"
else
  echo -e "${YELLOW}⚠ vastcsi/vast-mgmt secret not found — confirm block CSI is installed${NC}"
fi

ok "kubectl context: $(kubectl config current-context 2>/dev/null || echo unknown)"

if [ "$FAILS" -gt 0 ]; then
  echo -e "${RED}✗ Kubernetes preflight: $FAILS check(s) failed${NC}" >&2
  exit 1
fi

echo ""
echo -e "${YELLOW}▸ VAST data-plane (in-cluster pod → data VIPs)…${NC}"
echo -e "  image: ${AGENT_IMAGE}"
echo -e "  namespace: ${PREFLIGHT_NS}"

if ! kubectl get ns "$PREFLIGHT_NS" >/dev/null 2>&1; then
  fail "namespace $PREFLIGHT_NS missing — apply namespaces before preflight"
  exit 1
fi

POD="vastclaw-preflight-$(date +%s)"
MANIFEST="$(mktemp)"
cleanup_pod() {
  rm -f "$MANIFEST"
  kubectl -n "$PREFLIGHT_NS" delete pod "$POD" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  kubectl -n "$PREFLIGHT_NS" delete configmap vastclaw-preflight-script --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$PREFLIGHT_NS" delete secret vastclaw-preflight-config --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup_pod EXIT

kubectl -n "$PREFLIGHT_NS" create configmap vastclaw-preflight-script \
  --from-file=check_prereqs.py="$SCRIPT_DIR/check_prereqs.py" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$PREFLIGHT_NS" create secret generic vastclaw-preflight-config \
  --from-file=config.yaml="$CONFIG" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

cat >"$MANIFEST" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  namespace: ${PREFLIGHT_NS}
  labels:
    app.kubernetes.io/name: vastclaw-preflight
spec:
  restartPolicy: Never
  containers:
    - name: preflight
      image: ${AGENT_IMAGE}
      imagePullPolicy: IfNotPresent
      command: ["python3", "/preflight/check_prereqs.py", "data-plane", "/config/config.yaml"]
      volumeMounts:
        - name: script
          mountPath: /preflight
          readOnly: true
        - name: config
          mountPath: /config
          readOnly: true
  volumes:
    - name: script
      configMap:
        name: vastclaw-preflight-script
    - name: config
      secret:
        secretName: vastclaw-preflight-config
EOF
kubectl apply -f "$MANIFEST" >/dev/null

set +e
for _ in $(seq 1 90); do
  PHASE="$(kubectl -n "$PREFLIGHT_NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
  case "$PHASE" in
    Succeeded|Failed) break ;;
  esac
  sleep 2
done
PHASE="$(kubectl -n "$PREFLIGHT_NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
echo "── preflight pod logs ──"
kubectl -n "$PREFLIGHT_NS" logs "$POD" -c preflight 2>/dev/null || kubectl -n "$PREFLIGHT_NS" logs "$POD" 2>/dev/null || true
echo "────────────────────────"
set -e

if [ "$PHASE" != "Succeeded" ]; then
  echo -e "${RED}✗ in-cluster data-plane preflight failed (pod phase=$PHASE)${NC}" >&2
  kubectl -n "$PREFLIGHT_NS" describe pod "$POD" 2>/dev/null | tail -50 || true
  exit 1
fi

echo ""
echo -e "${GREEN}✓ preflight OK — ready to deploy${NC}"
exit 0
