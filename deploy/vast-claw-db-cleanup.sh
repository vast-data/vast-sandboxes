#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"
NS="vastclaw-observability"
DEPLOY="vastclaw-workflow-ui-backend"
SECRET="vastclaw-workflow-ui-config"
JOB="vastclaw-platform-cleanup"
LABEL="app.kubernetes.io/name=vastclaw-workflow-ui-backend"
DEFAULT_IMAGE="registry.example.com/vast-claw/vc-backend:latest"

command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }

DB_OK=0
K8S_OK=0

backend_image() {
  local img
  img=$(kubectl -n "$NS" get deploy "$DEPLOY" \
    -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
  echo "${img:-$DEFAULT_IMAGE}"
}

find_ready_backend() {
  local pod ready
  for pod in $(kubectl -n "$NS" get pods -l "$LABEL" \
    -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
    [ -z "$pod" ] && continue
    ready=$(kubectl -n "$NS" get pod "$pod" \
      -o jsonpath='{.status.containerStatuses[?(@.name=="backend")].ready}' 2>/dev/null || true)
    if [ "$ready" = "true" ]; then
      echo "$pod"
      return 0
    fi
  done
  return 1
}

exec_backend_module() {
  local module="$1"
  local pod out rc
  pod=$(find_ready_backend) || return 1
  set +e
  out=$(kubectl -n "$NS" exec "pod/$pod" -c backend -- python -m "$module" 2>&1)
  rc=$?
  set -e
  if [ -n "$out" ]; then echo "$out"; fi
  return "$rc"
}

run_cleanup_job() {
  local module="$1"
  local image out rc
  image=$(backend_image)

  if ! kubectl get namespace "$NS" >/dev/null 2>&1; then
    return 1
  fi
  if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
    echo -e "${YELLOW}  secret $SECRET not found — cannot run cleanup Job${NC}"
    return 1
  fi

  kubectl -n "$NS" delete job "$JOB" --ignore-not-found --wait=true 2>/dev/null || true

  echo -e "${YELLOW}  starting Job/$JOB ($module)...${NC}"
  kubectl -n "$NS" apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB
  namespace: $NS
  labels:
    app.kubernetes.io/part-of: vastclaw
spec:
  ttlSecondsAfterFinished: 300
  backoffLimit: 0
  template:
    metadata:
      labels:
        app.kubernetes.io/part-of: vastclaw
    spec:
      restartPolicy: Never
      containers:
        - name: cleanup
          image: $image
          imagePullPolicy: Always
          command: ["python", "-m", "$module"]
          volumeMounts:
            - name: config
              mountPath: /etc/secrets
              readOnly: true
      volumes:
        - name: config
          secret:
            secretName: $SECRET
EOF

  if kubectl -n "$NS" wait --for=condition=complete "job/$JOB" --timeout=180s >/dev/null 2>&1; then
    kubectl -n "$NS" logs "job/$JOB" 2>&1 || true
    rc=0
  else
    echo -e "${RED}  Job/$JOB failed:${NC}"
    kubectl -n "$NS" logs "job/$JOB" 2>&1 || true
    rc=1
  fi
  kubectl -n "$NS" delete job "$JOB" --ignore-not-found
  return "$rc"
}

wipe_vastclaw_db() {
  local out rc ready_pod
  echo -e "${YELLOW}  wiping vastclaw-db tables...${NC}"

  ready_pod=$(find_ready_backend 2>/dev/null || true)
  if [ -n "$ready_pod" ]; then
    set +e
    out=$(exec_backend_module vastclaw_ui.platform_cleanup)
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      echo -e "${GREEN}  ✓ vastclaw-db + k8s via platform_cleanup (backend pod)${NC}"
      DB_OK=1
      K8S_OK=1
      return 0
    fi

    set +e
    out=$(exec_backend_module vastclaw_ui.db_cleanup)
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      echo -e "${GREEN}  ✓ vastclaw-db via db_cleanup (backend pod)${NC}"
      DB_OK=1
      return 0
    fi

    echo -e "${YELLOW}  backend pod cleanup failed (exit $rc) — trying Job...${NC}"
  else
    echo -e "${YELLOW}  no ready backend pod (deploy missing or CrashLoop) — using cleanup Job...${NC}"
  fi

  if ! kubectl -n "$NS" get secret "$SECRET" >/dev/null 2>&1; then
    echo -e "${YELLOW}  secret $SECRET not found — backend wipe skipped${NC}"
    DB_OK=1
    return 0
  fi

  if run_cleanup_job vastclaw_ui.platform_cleanup; then
    echo -e "${GREEN}  ✓ vastclaw-db + k8s via platform_cleanup (Job)${NC}"
    DB_OK=1
    K8S_OK=1
    return 0
  fi

  if run_cleanup_job vastclaw_ui.db_cleanup; then
    echo -e "${GREEN}  ✓ vastclaw-db via db_cleanup (Job)${NC}"
    DB_OK=1
    return 0
  fi

  echo -e "${RED}  ✗ vastclaw-db wipe failed — schemas/tables may still contain data${NC}"
  return 1
}

if kubectl get namespace "$NS" >/dev/null 2>&1; then
  wipe_vastclaw_db || true
else
  echo -e "${YELLOW}  namespace $NS not found — skip vastclaw-db cleanup${NC}"
fi

if [ "$K8S_OK" != "1" ]; then
  echo -e "${YELLOW}  kubectl fallback (sandboxes, jobs, PVCs, retained PVs)...${NC}"
  chmod +x "$SCRIPT_DIR/vast-claw-k8s-cleanup.sh"
  "$SCRIPT_DIR/vast-claw-k8s-cleanup.sh"
  K8S_OK=1
fi

if [ "$DB_OK" != "1" ]; then
  exit 1
fi
