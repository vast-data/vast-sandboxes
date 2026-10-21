#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
CONFIG="${1:-}"
ACTION="${2:-ensure}"
STATUS_FILE="${3:-}"
NS="${VAST_TABLES_NS:-vastclaw-observability}"

if [ -z "$CONFIG" ] || [ ! -f "$CONFIG" ]; then
  echo -e "${RED}✗ usage: $0 <workflow-ui.config.yaml> [ensure|drop|drop-keep-corpus] [status-file]${NC}" >&2
  exit 2
fi
if [ "$ACTION" != "ensure" ] && [ "$ACTION" != "drop" ] && [ "$ACTION" != "drop-keep-corpus" ]; then
  echo -e "${RED}✗ action must be ensure, drop, or drop-keep-corpus${NC}" >&2
  exit 2
fi

IMAGE="$(vc_yaml_block_value "$CONFIG" runtime agent_image)"
if [ -z "$IMAGE" ]; then
  echo -e "${RED}✗ runtime.agent_image is empty${NC}" >&2
  exit 1
fi

POD="vastclaw-tables-$(date +%s)"
MANIFEST="$(mktemp)"
cleanup() {
  rm -f "$MANIFEST"
  kubectl -n "$NS" delete pod "$POD" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  kubectl -n "$NS" delete configmap vastclaw-tables-script --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$NS" delete secret vastclaw-tables-config --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [ "$ACTION" = "drop" ] || [ "$ACTION" = "drop-keep-corpus" ]; then
  echo -e "${YELLOW}▸ Dropping VastDB schemas and tables (in-cluster)…${NC}"
else
  echo -e "${YELLOW}▸ VastDB schemas and tables (in-cluster)…${NC}"
fi
echo "  image: ${IMAGE}"

kubectl -n "$NS" create configmap vastclaw-tables-script \
  --from-file=vast_tables.py="$SCRIPT_DIR/vast_tables.py" \
  --from-file=yaml_subset.py="$SCRIPT_DIR/yaml_subset.py" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" create secret generic vastclaw-tables-config \
  --from-file=config.yaml="$CONFIG" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

cat >"$MANIFEST" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  namespace: ${NS}
  labels:
    app.kubernetes.io/name: vastclaw-tables
spec:
  restartPolicy: Never
  containers:
    - name: tables
      image: ${IMAGE}
      imagePullPolicy: IfNotPresent
      command: ["python3", "-u", "/opt/vast_tables.py", "/config/config.yaml", "${ACTION}"]
      env:
        - name: PYTHONPATH
          value: /opt
      volumeMounts:
        - name: script
          mountPath: /opt
          readOnly: true
        - name: config
          mountPath: /config
          readOnly: true
  volumes:
    - name: script
      configMap:
        name: vastclaw-tables-script
    - name: config
      secret:
        secretName: vastclaw-tables-config
EOF
kubectl apply -f "$MANIFEST" >/dev/null

set +e
for _ in $(seq 1 90); do
  PHASE="$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
  case "$PHASE" in
    Succeeded|Failed) break ;;
  esac
  sleep 2
done
PHASE="$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
echo "── table bootstrap logs ──"
kubectl -n "$NS" logs "$POD" -c tables 2>/dev/null || kubectl -n "$NS" logs "$POD" 2>/dev/null || true
echo "──────────────────────────"
set -e

if [ "$PHASE" != "Succeeded" ]; then
  echo -e "${RED}✗ VastDB table ${ACTION} failed (pod phase=$PHASE)${NC}" >&2
  kubectl -n "$NS" describe pod "$POD" 2>/dev/null | tail -40 || true
  exit 1
fi
if [ "$ACTION" = "ensure" ] && [ -n "$STATUS_FILE" ]; then
  marker="$(kubectl -n "$NS" logs "$POD" -c tables 2>/dev/null | awk -F= '/^CORPUS_INGEST=/{v=$2} END{print v}')"
  [ -n "$marker" ] || marker="run"
  printf '%s\n' "$marker" >"$STATUS_FILE"
fi
if [ "$ACTION" = "drop" ] || [ "$ACTION" = "drop-keep-corpus" ]; then
  echo -e "${GREEN}✓ VastDB table drop finished${NC}"
else
  echo -e "${GREEN}✓ VastDB schemas and tables ready${NC}"
fi
