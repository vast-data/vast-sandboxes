#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
CONFIG="${1:-}"
NS="${VAST_CORPUS_NS:-vastclaw-observability}"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PKG="$REPO_ROOT/source-code/ingest/temp_ingest"
CORPUS_DIR="$REPO_ROOT/source-code/ingest/corpus/earnings21"

if [ -z "$CONFIG" ] || [ ! -f "$CONFIG" ]; then
  echo -e "${RED}✗ usage: $0 <workflow-ui.config.yaml>${NC}" >&2
  exit 2
fi

LOAD="$(vc_yaml_block_value "$CONFIG" platform corpus_load)"
if [ "$LOAD" != "true" ]; then
  echo -e "${GREEN}✓ corpus rows skipped (platform.corpus_load: false). The table is created empty.${NC}"
  exit 0
fi

if [ ! -d "$PKG" ]; then
  echo -e "${RED}✗ ingest package not found at $PKG${NC}" >&2
  exit 1
fi
if ! find "$CORPUS_DIR" -maxdepth 1 -name '*.parquet' | grep -q .; then
  echo -e "${RED}✗ no parquet in $CORPUS_DIR${NC}" >&2
  exit 1
fi

IMAGE="$(vc_yaml_block_value "$CONFIG" runtime agent_image)"
if [ -z "$IMAGE" ]; then
  echo -e "${RED}✗ runtime.agent_image is empty${NC}" >&2
  exit 1
fi

POD="vastclaw-corpus-$(date +%s)"
MANIFEST="$(mktemp)"
CORPUS_YAML="$(mktemp)"
cleanup() {
  rm -f "$MANIFEST" "$CORPUS_YAML"
  kubectl -n "$NS" delete pod "$POD" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  kubectl -n "$NS" delete configmap vastclaw-corpus-src --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$NS" delete configmap -l vastclaw/corpus=data --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$NS" delete secret vastclaw-corpus-config --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo -e "${YELLOW}▸ Earnings-21 ingest (in-cluster, all checked-in calls)…${NC}"
echo "  image: ${IMAGE}"
echo "  parquet: ${CORPUS_DIR}"

python3 "$SCRIPT_DIR/vast_platform.py" corpus-config "$CONFIG" "$CORPUS_YAML"

args=()
while IFS= read -r src; do
  args+=(--from-file="$(basename "$src")=$src")
done < <(find "$PKG" -maxdepth 1 -name '*.py' | sort)
kubectl -n "$NS" create configmap vastclaw-corpus-src \
  "${args[@]}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
CORPUS_MAPS=()
batch=()
batch_bytes=0
map_idx=0
flush_corpus_map() {
  [ "${#batch[@]}" -eq 0 ] && return 0
  local name="vastclaw-corpus-data-${map_idx}"
  local file_args=() src
  for src in "${batch[@]}"; do
    file_args+=(--from-file="$(basename "$src")=$src")
  done
  kubectl -n "$NS" delete configmap "$name" --ignore-not-found >/dev/null
  kubectl -n "$NS" create configmap "$name" "${file_args[@]}" >/dev/null
  kubectl -n "$NS" label configmap "$name" vastclaw/corpus=data >/dev/null
  CORPUS_MAPS+=("$name")
  map_idx=$((map_idx + 1))
  batch=()
  batch_bytes=0
}
while IFS= read -r src; do
  sz=$(wc -c <"$src" | tr -d ' ')
  if [ "$batch_bytes" -gt 0 ] && [ $((batch_bytes + sz)) -gt 450000 ]; then
    flush_corpus_map
  fi
  batch+=("$src")
  batch_bytes=$((batch_bytes + sz))
done < <(find "$CORPUS_DIR" -maxdepth 1 -name '*.parquet' | sort)
flush_corpus_map
if [ "${#CORPUS_MAPS[@]}" -eq 0 ]; then
  echo -e "${RED}✗ no corpus configmaps were created${NC}" >&2
  exit 1
fi
CORPUS_SOURCES=""
for name in "${CORPUS_MAPS[@]}"; do
  CORPUS_SOURCES+="          - configMap:
              name: ${name}
"
done
kubectl -n "$NS" create secret generic vastclaw-corpus-config \
  --from-file=ingest.yaml="$CORPUS_YAML" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

cat >"$MANIFEST" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  namespace: ${NS}
  labels:
    app.kubernetes.io/name: vastclaw-corpus
spec:
  restartPolicy: Never
  containers:
    - name: corpus
      image: ${IMAGE}
      imagePullPolicy: IfNotPresent
      command:
        - python3
        - -m
        - temp_ingest.run_earnings21_e2e
        - --skip-prepare
        - --data-dir
        - /corpus
      env:
        - name: PYTHONPATH
          value: /opt/ingest
        - name: VASTCLAW_CONFIG
          value: /config/ingest.yaml
      volumeMounts:
        - name: src
          mountPath: /opt/ingest/temp_ingest
          readOnly: true
        - name: config
          mountPath: /config
          readOnly: true
        - name: corpus
          mountPath: /corpus
          readOnly: true
  volumes:
    - name: src
      configMap:
        name: vastclaw-corpus-src
    - name: config
      secret:
        secretName: vastclaw-corpus-config
    - name: corpus
      projected:
        sources:
${CORPUS_SOURCES}
EOF
kubectl apply -f "$MANIFEST" >/dev/null

echo "  pod: ${POD}"
streamed=0
deadline=$((SECONDS + 2700))
PHASE="Pending"
while [ "$SECONDS" -lt "$deadline" ]; do
  PHASE="$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
  case "$PHASE" in
    Succeeded|Failed) break ;;
  esac
  if [ "$PHASE" = "Running" ]; then
    streamed=1
    kubectl -n "$NS" logs -f "$POD" -c corpus || true
    sleep 2
    continue
  fi
  echo "  waiting for pod (${PHASE})"
  sleep 5
done
PHASE="$(kubectl -n "$NS" get pod "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo Unknown)"
if [ "$streamed" -eq 0 ]; then
  echo "── corpus ingest logs ──"
  kubectl -n "$NS" logs "$POD" -c corpus 2>/dev/null || true
  echo "────────────────────────"
fi

if [ "$PHASE" != "Succeeded" ]; then
  echo -e "${RED}✗ corpus ingest failed (pod phase=$PHASE)${NC}" >&2
  kubectl -n "$NS" describe pod "$POD" 2>/dev/null | tail -40 || true
  exit 1
fi
echo -e "${GREEN}✓ corpus rows loaded${NC}"
