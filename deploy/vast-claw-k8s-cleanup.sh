#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found"; exit 1; }

discover_departments() {
  local ns dept
  for ns in $(kubectl get ns -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
    | grep '^vastclaw-' | grep -v '^vastclaw-observability$' || true); do
    dept="${ns#vastclaw-}"
    echo "$dept"
  done
}

purge_namespace() {
  local dept="$1" ns="vastclaw-$dept"
  kubectl get namespace "$ns" >/dev/null 2>&1 || return 0

  echo -e "${YELLOW}  purging $ns (sandboxes, jobs, pods, PVCs, PVs, secrets)...${NC}"

  kubectl delete sandboxes.agents.x-k8s.io -n "$ns" --all \
    --ignore-not-found --wait=false 2>/dev/null || true

  kubectl delete jobs -n "$ns" -l vastclaw.vastdata.com/workflow \
    --ignore-not-found --wait=false 2>/dev/null || true

  kubectl delete pods -n "$ns" -l vastclaw.vastdata.com/workflow \
    --force --grace-period=0 --ignore-not-found 2>/dev/null || true

  local pvc pv
  while IFS=$'\t' read -r pvc pv; do
    [ -z "$pvc" ] && continue
    kubectl delete pvc "$pvc" -n "$ns" --ignore-not-found --wait=false 2>/dev/null || true
    if [ -n "$pv" ]; then
      kubectl delete pv "$pv" --ignore-not-found --wait=false 2>/dev/null || true
    fi
  done < <(kubectl get pvc -n "$ns" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.volumeName}{"\n"}{end}' 2>/dev/null || true)

  kubectl delete secret vastclaw-agent-runner vastclaw-archive-runner -n "$ns" \
    --ignore-not-found 2>/dev/null || true

  # Orphaned PVs left in Released/Available by Retain StorageClasses.
  local orphan
  while IFS= read -r orphan; do
    [ -z "$orphan" ] && continue
    kubectl delete pv "$orphan" --ignore-not-found --wait=false 2>/dev/null || true
  done < <(kubectl get pv -o json 2>/dev/null | python3 -c "
import json, sys
ns, sc = '$ns', '$ns'
for pv in json.load(sys.stdin).get('items', []):
    phase = (pv.get('status') or {}).get('phase')
    if phase not in ('Released', 'Available'):
        continue
    spec = pv.get('spec') or {}
    claim = spec.get('claimRef') or {}
    if spec.get('storageClassName') == sc or claim.get('namespace') == ns:
        print(pv['metadata']['name'])
" 2>/dev/null || true)

  echo -e "${GREEN}  ✓ $ns purged${NC}"
}

DEPTS="$(discover_departments)"
if [ -z "$DEPTS" ]; then
  echo -e "${YELLOW}  no vastclaw department namespaces found — skip k8s purge${NC}"
  exit 0
fi

while IFS= read -r dept; do
  [ -z "$dept" ] && continue
  purge_namespace "$dept"
done <<< "$DEPTS"
