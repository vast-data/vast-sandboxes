#!/bin/bash

set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

NS="${NS:-vastclaw-snapshot-test}"
SC="${SC:-vastdata-block}"
SNAP_CLASS="${SNAP_CLASS:-${SC}-snapshot}"
MARKER="PARENT_DATA_123"

usage() {
  echo "Usage: ./deploy/vast-claw-snapshot-smoke.sh            (run proofs)"
  echo "       ./deploy/vast-claw-snapshot-smoke.sh cleanup    (delete $NS)"
  echo "  NS=$NS  SC=$SC  SNAP_CLASS=$SNAP_CLASS"
}

case "${1:-run}" in
  cleanup|--cleanup|-d) kubectl delete ns "$NS" --ignore-not-found; echo -e "${GREEN}✓ cleaned $NS${NC}"; exit 0 ;;
  run|"") ;;
  -h|--help) usage; exit 0 ;;
  *) echo -e "${RED}unknown: $1${NC}"; usage; exit 1 ;;
esac

command -v kubectl >/dev/null || { echo -e "${RED}kubectl required${NC}"; exit 1; }
kubectl cluster-info >/dev/null 2>&1 || { echo -e "${RED}kubectl cannot reach cluster${NC}"; exit 1; }

fail() { echo -e "${RED}✗ $*${NC}"; exit 1; }
pass() { echo -e "${GREEN}✓ $*${NC}"; }

echo "🧪 VAST block snapshot smoke — namespace $NS"
echo "============================================"

kubectl get sc "$SC" >/dev/null 2>&1 || fail "StorageClass $SC missing — run deploy/csi/vast-block-csi.sh"
kubectl get volumesnapshotclass "$SNAP_CLASS" >/dev/null 2>&1 || \
  fail "VolumeSnapshotClass $SNAP_CLASS missing — re-run deploy/csi/vast-block-csi.sh"

kubectl create ns "$NS" 2>/dev/null || true

echo -e "\n${YELLOW}A) PVC clone${NC}"
kubectl apply -f - <<YAML
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: clone-parent
  namespace: $NS
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: $SC
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: clone-parent-writer
  namespace: $NS
spec:
  restartPolicy: Never
  containers:
    - name: w
      image: busybox:1.36
      command: ["sh", "-c", "echo -n '$MARKER' > /data/marker.txt && sleep 3600"]
      volumeMounts:
        - { name: data, mountPath: /data }
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: clone-parent
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/clone-parent-writer --timeout=180s
kubectl -n "$NS" exec clone-parent-writer -- cat /data/marker.txt | grep -q "$MARKER" || fail "parent write"
kubectl -n "$NS" exec clone-parent-writer -- sync

kubectl apply -f - <<YAML
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: clone-child
  namespace: $NS
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: $SC
  dataSource:
    name: clone-parent
    kind: PersistentVolumeClaim
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: clone-child-reader
  namespace: $NS
spec:
  restartPolicy: Never
  containers:
    - name: r
      image: busybox:1.36
      command: ["sh", "-c", "cat /data/marker.txt"]
      volumeMounts:
        - { name: data, mountPath: /data }
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: clone-child
YAML
kubectl -n "$NS" wait --for=condition=Ready pod/clone-child-reader --timeout=180s 2>/dev/null || true
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/clone-child-reader --timeout=180s
CLONE_OUT="$(kubectl -n "$NS" logs clone-child-reader)"
[ "$CLONE_OUT" = "$MARKER" ] || fail "clone read got '$CLONE_OUT', want '$MARKER'"
pass "PVC clone: clone-child-reader => $CLONE_OUT"

echo -e "\n${YELLOW}B) VolumeSnapshot checkpoint + restore${NC}"
kubectl apply -f - <<YAML
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: clone-parent-snap
  namespace: $NS
spec:
  volumeSnapshotClassName: $SNAP_CLASS
  source:
    persistentVolumeClaimName: clone-parent
YAML
deadline=$((SECONDS + 120))
ready=""
while [ $SECONDS -lt $deadline ]; do
  ready="$(kubectl -n "$NS" get volumesnapshot clone-parent-snap -o jsonpath='{.status.readyToUse}' 2>/dev/null || true)"
  [ "$ready" = "true" ] && break
  sleep 2
done
[ "$ready" = "true" ] || fail "snapshot not readyToUse within 120s"
pass "VolumeSnapshot readyToUse=true"

kubectl -n "$NS" exec clone-parent-writer -- sh -c "echo -n CORRUPTED > /data/marker.txt && sync"

kubectl apply -f - <<YAML
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: clone-restored
  namespace: $NS
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: $SC
  dataSource:
    name: clone-parent-snap
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: restored-reader
  namespace: $NS
spec:
  restartPolicy: Never
  containers:
    - name: r
      image: busybox:1.36
      command: ["sh", "-c", "cat /data/marker.txt"]
      volumeMounts:
        - { name: data, mountPath: /data }
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: clone-restored
YAML
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/restored-reader --timeout=180s
RESTORED="$(kubectl -n "$NS" logs restored-reader)"
[ "$RESTORED" = "$MARKER" ] || fail "restored read got '$RESTORED', want '$MARKER'"
pass "snapshot restore: restored-reader => $RESTORED"

echo -e "\n${YELLOW}C) Sleep/wake (remote block reattach)${NC}"
kubectl -n "$NS" delete pod clone-parent-writer --ignore-not-found
kubectl apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: clone-parent-waker
  namespace: $NS
spec:
  restartPolicy: Never
  containers:
    - name: w
      image: busybox:1.36
      command: ["sh", "-c", "cat /data/marker.txt"]
      volumeMounts:
        - { name: data, mountPath: /data }
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: clone-parent
YAML
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/clone-parent-waker --timeout=180s
WAKE="$(kubectl -n "$NS" logs clone-parent-waker)"
[ "$WAKE" = "CORRUPTED" ] || fail "sleep/wake got '$WAKE', want CORRUPTED (live volume state)"
pass "sleep/wake: clone-parent-waker => $WAKE (live PVC persisted across pod delete)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
pass "All three VAST block storage proofs passed in $NS"
echo "  A clone-on-spawn   B snapshot rollback   C remote-block sleep/wake"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Cleanup: ./deploy/vast-claw-snapshot-smoke.sh cleanup"
