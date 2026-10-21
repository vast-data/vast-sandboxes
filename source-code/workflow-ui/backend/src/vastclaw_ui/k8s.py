from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from vastclaw_ui.models import (
    AgentLiveness,
    DepartmentHealth,
    DepartmentStorageInventory,
    DeploymentHealth,
    RestartResult,
    SandboxStat,
    WorkflowSandboxRow,
)


class ClusterControl(Protocol):
    def health(self, departments: list[str]) -> list[DepartmentHealth]: ...

    def restart_department(self, department: str) -> RestartResult: ...

    def sandbox_stats(self, departments: list[str]) -> list[SandboxStat]: ...

    def storage_inventory(self, departments: list[str]) -> list[DepartmentStorageInventory]: ...

    def agent_liveness(self, department: str, workflow_id: str) -> list[AgentLiveness]: ...

    def find_agent_pod(self, department: str, workflow_id: str, slug: str) -> str: ...

    def open_exec(self, department: str, pod: str) -> Any: ...


def _namespace(department: str) -> str:
    return f"vastclaw-{department}"


def slug_for(name: str) -> str:
    """Sub-agent name → the DNS-safe slug used in sandbox, pod and PVC names."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug[:30].rstrip("-") or "subagent"


_SANDBOX_NAME_MAX = 19


def sandbox_pod_name(workflow_id: str, subagent: str) -> str:
    """The pod/sandbox name for one sub-agent."""
    slug = slug_for(subagent)
    readable = f"{workflow_id}-{slug}".rstrip("-")
    if len(readable) <= _SANDBOX_NAME_MAX:
        return readable
    tag = hashlib.sha256(slug.encode()).hexdigest()[:3]
    room = _SANDBOX_NAME_MAX - 1 - len(tag)
    base = (workflow_id[:room] or "wf").rstrip("-")
    return f"{base}-{tag}"


_UNITS = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "Pi": 1024**5,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
}


def _parse_quantity(q: str | None) -> int:
    """Kubernetes storage quantity (e.g. '1Gi', '500Mi') -> bytes. Best-effort."""
    if not q:
        return 0
    q = q.strip()
    for suffix, factor in sorted(_UNITS.items(), key=lambda kv: -len(kv[0])):
        if q.endswith(suffix):
            try:
                return int(float(q[: -len(suffix)]) * factor)
            except ValueError:
                return 0
    try:
        return int(float(q))
    except ValueError:
        return 0


def _human_bytes(n: int) -> str:
    if n <= 0:
        return "0"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if n < 1024 or unit == "PiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PiB"


def _pvc_clone_source(pvc: Any) -> str:
    labels = (pvc.metadata.labels or {}) if pvc.metadata else {}
    annotations = (pvc.metadata.annotations or {}) if pvc.metadata else {}
    clone_src = (
        labels.get("vastclaw.vastdata.com/clone-source")
        or annotations.get("vastclaw.vastdata.com/clone-source")
        or ""
    )
    if not clone_src and pvc.spec and pvc.spec.data_source:
        if pvc.spec.data_source.kind == "PersistentVolumeClaim":
            clone_src = pvc.spec.data_source.name or ""
    return clone_src


def _namespace_storage_inventory(
    core: Any, custom: Any, dept: str
) -> tuple[SandboxStat, dict[str, WorkflowSandboxRow], str]:
    """Live PVC / snapshot / pod picture for one department, grouped by workflow."""
    from kubernetes import client

    ns = _namespace(dept)
    stat = SandboxStat(department=dept, namespace=ns)
    rows: dict[str, WorkflowSandboxRow] = {}
    classes: set[str] = set()
    error = ""
    try:
        pvcs = core.list_namespaced_persistent_volume_claim(
            namespace=ns, label_selector="vastclaw.vastdata.com/workflow"
        ).items
        for pvc in pvcs:
            labels = (pvc.metadata.labels or {}) if pvc.metadata else {}
            role = labels.get("vastclaw.vastdata.com/role", "")
            wf = labels.get("vastclaw.vastdata.com/workflow", "")
            row = None
            if wf:
                row = rows.setdefault(wf, WorkflowSandboxRow(workflow_id=wf, head_pvc=wf))

            stat.pvc_total += 1
            if row:
                row.pvc_total += 1
            phase = (pvc.status.phase if pvc.status else "") or ""
            if phase == "Bound":
                stat.pvc_bound += 1
                if row:
                    row.pvc_bound += 1
            elif phase == "Pending":
                stat.pvc_pending += 1
                if row:
                    row.pvc_pending += 1

            is_subagent = bool(labels.get("vastclaw.vastdata.com/subagent")) or role == "subagent"
            if is_subagent:
                stat.subagent_pvcs += 1
                if row:
                    row.subagent_pvcs += 1
            elif role != "restore":
                stat.head_pvcs += 1

            if _pvc_clone_source(pvc):
                stat.cloned_pvcs += 1
                if row:
                    row.cloned_pvcs += 1
            if role == "restore" or labels.get("vastclaw.vastdata.com/restore-source"):
                stat.restored_pvcs += 1
                if row:
                    row.restored_pvcs += 1

            if pvc.spec and pvc.spec.storage_class_name:
                classes.add(pvc.spec.storage_class_name)
            req = None
            if pvc.spec and pvc.spec.resources and pvc.spec.resources.requests:
                req = pvc.spec.resources.requests.get("storage")
            cap = _parse_quantity(req)
            stat.capacity_bytes += cap
            if row:
                row.capacity_bytes += cap

        try:
            snaps = custom.list_namespaced_custom_object(
                group="snapshot.storage.k8s.io",
                version="v1",
                namespace=ns,
                plural="volumesnapshots",
                label_selector="vastclaw.vastdata.com/workflow",
            )
            for item in snaps.get("items", []):
                stat.volume_snapshots += 1
                ready = bool((item.get("status") or {}).get("readyToUse"))
                if ready:
                    stat.snapshots_ready += 1
                meta = item.get("metadata") or {}
                wf = (meta.get("labels") or {}).get("vastclaw.vastdata.com/workflow", "")
                if wf:
                    row = rows.setdefault(wf, WorkflowSandboxRow(workflow_id=wf, head_pvc=wf))
                    row.volume_snapshots += 1
                    if ready:
                        row.snapshots_ready += 1
        except client.exceptions.ApiException as snap_exc:
            if snap_exc.status not in (404, 403):
                raise

        known = sorted(rows, key=len, reverse=True)
        for pod in core.list_namespaced_pod(namespace=ns).items:
            phase = (pod.status.phase if pod.status else "") or ""
            if phase != "Running":
                continue
            name = (pod.metadata.name if pod.metadata else "") or ""
            wf = next((w for w in known if name.startswith(f"{w}-")), "")
            if not wf:
                continue  # gateway / MCP / helper pod, not a sub-agent sandbox
            stat.active_agents += 1
            rows[wf].active_agents += 1

        stat.workflows = len(rows)
        stat.storage_class = ", ".join(sorted(classes))
        stat.capacity_human = _human_bytes(stat.capacity_bytes)
        for row in rows.values():
            row.capacity_human = _human_bytes(row.capacity_bytes)
    except Exception as exc:
        error = str(exc)
        stat.error = error
    return stat, rows, error


class KubernetesControl:
    """Read deployment health and trigger rollout restarts, scoped to
    vastclaw-<dept> namespaces. Uses only get/list/patch on deployments."""

    def __init__(self, in_cluster: bool = True) -> None:
        self._in_cluster = in_cluster
        self._apps: Any = None

    def _api(self) -> Any:
        if self._apps is None:
            from kubernetes import client, config

            if self._in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config()
            self._apps = client.AppsV1Api()
        return self._apps

    def health(self, departments: list[str]) -> list[DepartmentHealth]:
        api = self._api()
        out: list[DepartmentHealth] = []
        for dept in departments:
            ns = _namespace(dept)
            entry = DepartmentHealth(department=dept, namespace=ns)
            try:
                deployments = api.list_namespaced_deployment(namespace=ns).items
                for dep in deployments:
                    desired = dep.spec.replicas or 0
                    ready = (dep.status.ready_replicas or 0) if dep.status else 0
                    entry.deployments.append(
                        DeploymentHealth(name=dep.metadata.name, ready=ready, desired=desired)
                    )
                entry.healthy = len(entry.deployments) > 0 and all(
                    d.ready >= d.desired and d.desired > 0 for d in entry.deployments
                )
            except Exception as exc:  # namespace missing / API error -> report, don't crash
                entry.error = str(exc)
            out.append(entry)
        return out

    def sandbox_stats(self, departments: list[str]) -> list[SandboxStat]:
        from kubernetes import client

        self._api()
        core = client.CoreV1Api()
        custom = client.CustomObjectsApi()
        out: list[SandboxStat] = []
        for dept in departments:
            stat, _, _ = _namespace_storage_inventory(core, custom, dept)
            out.append(stat)
        return out

    def storage_inventory(self, departments: list[str]) -> list[DepartmentStorageInventory]:
        from kubernetes import client

        self._api()
        core = client.CoreV1Api()
        custom = client.CustomObjectsApi()
        out: list[DepartmentStorageInventory] = []
        for dept in departments:
            stat, rows, error = _namespace_storage_inventory(core, custom, dept)
            workflows = sorted(
                rows.values(),
                key=lambda r: (-r.active_agents, -r.pvc_total, r.workflow_id),
            )
            out.append(
                DepartmentStorageInventory(
                    department=dept,
                    namespace=stat.namespace,
                    summary=stat,
                    workflows=workflows,
                    error=error,
                )
            )
        return out

    def agent_liveness(self, department: str, workflow_id: str) -> list[AgentLiveness]:
        from kubernetes import client

        self._api()
        core = client.CoreV1Api()
        ns = _namespace(department)
        out: list[AgentLiveness] = []
        prefix = f"{workflow_id}-"
        pods = core.list_namespaced_pod(namespace=ns).items
        seen: set[str] = set()
        for pod in pods:
            name = (pod.metadata.name if pod.metadata else "") or ""
            if not name.startswith(prefix):
                continue
            slug = ""
            spec = pod.spec
            containers = []
            if spec is not None:
                containers = list(spec.containers or []) + list(spec.init_containers or [])
            for container in containers:
                for env in container.env or []:
                    if env.name == "SUBAGENT_SLUG" and env.value:
                        slug = env.value
                        break
                if slug:
                    break
            if not slug:
                slug = name[len(prefix) :]
            if not slug or slug in seen:
                continue
            seen.add(slug)
            phase = (pod.status.phase if pod.status else "") or ""
            alive = phase == "Running"
            out.append(
                AgentLiveness(
                    slug=slug,
                    alive=alive,
                    pod=pod.metadata.name if alive else "",
                    phase=phase,
                )
            )
        return out

    def find_agent_pod(self, department: str, workflow_id: str, slug: str) -> str:
        """Name of the currently-Running pod for one sub-agent sandbox, or "" if none."""
        from kubernetes import client

        self._api()
        core = client.CoreV1Api()
        ns = _namespace(department)
        want = sandbox_pod_name(workflow_id, slug)
        for pod in core.list_namespaced_pod(namespace=ns).items:
            name = (pod.metadata.name if pod.metadata else "") or ""
            if name != want:
                continue
            phase = (pod.status.phase if pod.status else "") or ""
            if phase == "Running":
                return name
        return ""

    def open_exec(self, department: str, pod: str) -> Any:
        """Open a streaming exec channel (a login shell) into the agent's sandbox pod."""
        from kubernetes import client
        from kubernetes.stream import stream

        self._api()
        core = client.CoreV1Api()
        ns = _namespace(department)
        return stream(
            core.connect_get_namespaced_pod_exec,
            pod,
            ns,
            container="agent",
            command=["/bin/sh", "-c", "exec /bin/bash 2>/dev/null || exec /bin/sh"],
            stderr=True,
            stdin=True,
            stdout=True,
            tty=True,
            _preload_content=False,
        )

    def restart_department(self, department: str) -> RestartResult:
        api = self._api()
        ns = _namespace(department)
        stamp = datetime.now(UTC).isoformat()
        body = {
            "spec": {
                "template": {
                    "metadata": {"annotations": {"vastclaw.vastdata.com/restartedAt": stamp}}
                }
            }
        }
        result = RestartResult(department=department)
        deployments = api.list_namespaced_deployment(namespace=ns).items
        for dep in deployments:
            api.patch_namespaced_deployment(name=dep.metadata.name, namespace=ns, body=body)
            result.restarted.append(dep.metadata.name)
        return result
