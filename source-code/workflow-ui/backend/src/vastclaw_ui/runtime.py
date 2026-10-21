from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Protocol

import yaml
from pydantic import BaseModel

from vastclaw_ui.config import AgentSandboxSettings, SandboxSettings, Settings
from vastclaw_ui.department_prompts import (
    department_span_attrs,
    planning_lead_prompt,
    planning_user_goal,
    span_vocab,
    synthesis_system_prompt,
    synthesis_user_prompt,
)
from vastclaw_ui.history import HistoryStore
from vastclaw_ui.llm import ModelError, NimClient
from vastclaw_ui.store import Span, SpanSource

_log = logging.getLogger(__name__)

_RUNNER_SECRET = "vastclaw-agent-runner"


def _pvc_resources(client: object, storage: str) -> object:
    """Storage request for a PersistentVolumeClaim."""
    requests = {"storage": storage}
    volume_cls = getattr(client, "V1VolumeResourceRequirements", None)
    if volume_cls is not None:
        return volume_cls(requests=requests)
    return client.V1ResourceRequirements(requests=requests)  # type: ignore[attr-defined]

_TRANSIENT_SNAPSHOT_MARKERS = (
    "not_allowed",
    "service_unavailable",
    "503",
    "429",
    "too many",
    "timeout",
    "timed out",
    "temporarily",
    "try again",
)


class _TransientSnapshotError(RuntimeError):
    """Snapshot was rejected/stalled by VMS but is worth retrying."""


def _is_transient_snapshot_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _TRANSIENT_SNAPSHOT_MARKERS)


class Sandbox(BaseModel):
    pvc_name: str
    view_path: str


class RunRequest(BaseModel):
    run_id: str
    workflow_id: str
    turn_id: str = ""
    department: str
    user: str = "unknown"
    prompt: str
    sandbox: Sandbox
    # When true, fan out sub-agents immediately even if require_plan_approval is on.
    auto_approve_plan: bool = False


class SandboxProvisioner(Protocol):
    def ensure_workflow_sandbox(self, department: str, workflow_id: str) -> Sandbox: ...

    def ensure_subagent_sandbox(
        self,
        department: str,
        workflow_id: str,
        subagent: str,
        *,
        clone_from: str | None = None,
    ) -> Sandbox: ...

    def list_workflow_pvcs(self, department: str, workflow_id: str) -> list[str]: ...

    def delete_workflow_pvcs(self, department: str, workflow_id: str) -> list[str]: ...

    def delete_department_pvcs(self, department: str) -> list[str]: ...

    def seed_workflow_head(
        self,
        department: str,
        workflow_id: str,
        *,
        run_id: str = "",
        prompt: str = "",
        plan_json: str = "[]",
    ) -> None: ...

    def create_volume_snapshot(
        self,
        department: str,
        workflow_id: str,
        pvc_name: str,
        *,
        run_id: str = "",
        label: str = "",
        name_hint: str = "",
    ) -> str: ...

    def list_volume_snapshots(self, department: str, workflow_id: str) -> list[dict]: ...

    def restore_pvc_from_snapshot(
        self,
        department: str,
        workflow_id: str,
        snapshot_name: str,
        pvc_name: str,
        *,
        subagent: str = "",
    ) -> str: ...

    def get_storage_state(self, department: str, workflow_id: str) -> dict: ...

    def sandboxes_alive(self, department: str, workflow_id: str) -> bool: ...

    def wake_for_continue(self, department: str, workflow_id: str) -> dict: ...

    def delete_workflow_resources(
        self, department: str, workflow_id: str, *, agent_sandbox: AgentSandboxSettings
    ) -> dict[str, list[str]]: ...


class NemoClawClient(Protocol):
    def start_run(self, request: RunRequest) -> None: ...

    def resume_run(self, request: RunRequest, plan: list[dict[str, str]]) -> None: ...

    def begin_question(self, request: RunRequest) -> None: ...

    def answer_question(self, request: RunRequest) -> None: ...


class InMemoryProvisioner:
    def __init__(self, settings: SandboxSettings) -> None:
        self._settings = settings
        self.created: list[Sandbox] = []

    def ensure_workflow_sandbox(self, department: str, workflow_id: str) -> Sandbox:
        sandbox = Sandbox(
            pvc_name=workflow_id,
            view_path=f"{self._settings.department_view_root}/{department}/workflows/{workflow_id}",
        )
        self.created.append(sandbox)
        return sandbox

    def list_workflow_pvcs(self, department: str, workflow_id: str) -> list[str]:
        return [s.pvc_name for s in self.created if s.pvc_name.startswith(workflow_id)]

    def delete_workflow_pvcs(self, department: str, workflow_id: str) -> list[str]:
        deleted = self.list_workflow_pvcs(department, workflow_id)
        self.created = [s for s in self.created if not s.pvc_name.startswith(workflow_id)]
        return deleted

    def delete_department_pvcs(self, department: str) -> list[str]:
        deleted = [s.pvc_name for s in self.created]
        self.created = []
        return deleted

    def ensure_subagent_sandbox(
        self,
        department: str,
        workflow_id: str,
        subagent: str,
        *,
        clone_from: str | None = None,
    ) -> Sandbox:
        return self.ensure_workflow_sandbox(department, f"{workflow_id}-{subagent}")

    def seed_workflow_head(
        self,
        department: str,
        workflow_id: str,
        *,
        run_id: str = "",
        prompt: str = "",
        plan_json: str = "[]",
    ) -> None:
        return None

    def create_volume_snapshot(
        self,
        department: str,
        workflow_id: str,
        pvc_name: str,
        *,
        run_id: str = "",
        label: str = "",
        name_hint: str = "",
    ) -> str:
        hint = (name_hint or label or "chk").replace("/", "-")[:20]
        return f"snap-{workflow_id}-{hint}"

    def list_volume_snapshots(self, department: str, workflow_id: str) -> list[dict]:
        return []

    def restore_pvc_from_snapshot(
        self,
        department: str,
        workflow_id: str,
        snapshot_name: str,
        pvc_name: str,
        *,
        subagent: str = "",
    ) -> str:
        return pvc_name

    def get_storage_state(self, department: str, workflow_id: str) -> dict:
        pvcs = self.list_workflow_pvcs(department, workflow_id)
        return {
            "workflow_id": workflow_id,
            "department": department,
            "head_pvc": workflow_id,
            "pvcs": [{"name": n, "role": "head" if n == workflow_id else "subagent"} for n in pvcs],
            "snapshots": [],
            "clone_enabled": self._settings.clone_subagents_from_head,
            "checkpoint_enabled": self._settings.checkpoint_before_fanout,
            "checkpoint_on_subagent_complete": self._settings.checkpoint_on_subagent_complete,
            "wake_on_continue": self._settings.wake_on_continue,
            "snapshot_class": self._settings.snapshot_class or "",
            "last_checkpoint": "",
        }

    def sandboxes_alive(self, department: str, workflow_id: str) -> bool:
        # Local/demo: treat sandboxes as live so wake/reap paths stay k8s-only.
        return True

    def wake_for_continue(self, department: str, workflow_id: str) -> dict:
        return {
            "woke": False,
            "head_action": "noop",
            "head_snapshot": "",
            "actions": [],
        }

    def delete_workflow_resources(
        self, department: str, workflow_id: str, *, agent_sandbox: AgentSandboxSettings
    ) -> dict[str, list[str]]:
        pvcs = self.delete_workflow_pvcs(department, workflow_id)
        return {"sandboxes": [], "jobs": [], "snapshots": [], "pvcs": pvcs}


class RecordingNemoClawClient:
    def __init__(self) -> None:
        self.runs: list[RunRequest] = []
        self.resumed: list[tuple[RunRequest, list[dict[str, str]]]] = []
        self.begun: list[RunRequest] = []
        self.questions: list[RunRequest] = []

    def start_run(self, request: RunRequest) -> None:
        self.runs.append(request)

    def resume_run(self, request: RunRequest, plan: list[dict[str, str]]) -> None:
        self.resumed.append((request, plan))

    def begin_question(self, request: RunRequest) -> None:
        self.begun.append(request)

    def answer_question(self, request: RunRequest) -> None:
        self.questions.append(request)


class KubernetesProvisioner:
    _SNAPSHOT_GROUP = "snapshot.storage.k8s.io"
    _SNAPSHOT_VERSION = "v1"
    _SNAPSHOT_PLURAL = "volumesnapshots"

    def __init__(self, settings: SandboxSettings) -> None:
        self._settings = settings
        self._snapshot_slots = threading.Semaphore(
            max(1, settings.max_concurrent_snapshots)
        )

    def _load_kube(self) -> None:
        from kubernetes import config

        if self._settings.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()

    def _snapshot_class_name(self) -> str:
        if self._settings.snapshot_class:
            return self._settings.snapshot_class
        sc = self._settings.storage_class
        return f"{sc}-snapshot" if sc else "vastdata-block-snapshot"

    def ensure_workflow_sandbox(self, department: str, workflow_id: str) -> Sandbox:
        from kubernetes import client

        self._load_kube()

        namespace = f"vastclaw-{department}"
        pvc_name = workflow_id
        api = client.CoreV1Api()
        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                labels={
                    "vastclaw.vastdata.com/department": department,
                    "vastclaw.vastdata.com/workflow": workflow_id,
                    "vastclaw.vastdata.com/role": "head",
                },
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=[self._settings.access_mode],
                storage_class_name=(self._settings.storage_class or namespace),
                resources=_pvc_resources(client, self._settings.storage_request),
            ),
        )
        try:
            api.create_namespaced_persistent_volume_claim(namespace=namespace, body=body)
        except client.exceptions.ApiException as exc:
            if exc.status != 409:  # already exists -> reuse
                raise
        view_path = f"{self._settings.department_view_root}/{department}/workflows/{workflow_id}"
        return Sandbox(pvc_name=pvc_name, view_path=view_path)

    def ensure_subagent_sandbox(
        self,
        department: str,
        workflow_id: str,
        subagent: str,
        *,
        clone_from: str | None = None,
    ) -> Sandbox:
        from kubernetes import client

        self._load_kube()

        namespace = f"vastclaw-{department}"
        pvc_name = f"{workflow_id}-{subagent}"[:63].rstrip("-")
        source = clone_from
        if source is None and self._settings.clone_subagents_from_head:
            source = workflow_id
        labels = {
            "vastclaw.vastdata.com/department": department,
            "vastclaw.vastdata.com/workflow": workflow_id,
            "vastclaw.vastdata.com/subagent": subagent,
            "vastclaw.vastdata.com/role": "subagent",
        }
        annotations: dict[str, str] = {}
        if source:
            labels["vastclaw.vastdata.com/clone-source"] = source
            annotations["vastclaw.vastdata.com/clone-source"] = source
        spec_kwargs: dict = {
            "access_modes": [self._settings.access_mode],
            "storage_class_name": (self._settings.storage_class or namespace),
            "resources": _pvc_resources(client, self._settings.storage_request),
        }
        if source:
            spec_kwargs["data_source"] = client.V1TypedLocalObjectReference(
                name=source,
                kind="PersistentVolumeClaim",
            )
        api = client.CoreV1Api()
        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                labels=labels,
                annotations=annotations,
            ),
            spec=client.V1PersistentVolumeClaimSpec(**spec_kwargs),
        )
        try:
            api.create_namespaced_persistent_volume_claim(namespace=namespace, body=body)
        except client.exceptions.ApiException as exc:
            if exc.status != 409:
                raise
        view_path = (
            f"{self._settings.department_view_root}/{department}"
            f"/workflows/{workflow_id}/subagents/{subagent}"
        )
        return Sandbox(pvc_name=pvc_name, view_path=view_path)

    def seed_workflow_head(
        self,
        department: str,
        workflow_id: str,
        *,
        run_id: str = "",
        prompt: str = "",
        plan_json: str = "[]",
    ) -> None:
        """Write a small run-context seed onto the head PVC so sub-agent clones inherit it."""
        from kubernetes import client

        if not self._settings.seed_head_before_fanout:
            return
        self._load_kube()
        namespace = f"vastclaw-{department}"
        mount = "/workspace"
        payload = json.dumps(
            {
                "workflow_id": workflow_id,
                "run_id": run_id,
                "prompt": prompt,
                "plan": json.loads(plan_json) if plan_json else [],
                "seeded_at": _now(),
            },
            indent=2,
        )
        job_name = f"seed-{workflow_id}"[:63].rstrip("-")
        body = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                labels={
                    "vastclaw.vastdata.com/workflow": workflow_id,
                    "vastclaw.vastdata.com/role": "seed",
                },
            ),
            spec=client.V1JobSpec(
                ttl_seconds_after_finished=120,
                backoff_limit=1,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        containers=[
                            client.V1Container(
                                name="seed",
                                image="busybox:1.36",
                                env=[
                                    client.V1EnvVar(name="SEED_JSON", value=payload),
                                ],
                                command=[
                                    "sh",
                                    "-c",
                                    (
                                        f"mkdir -p {mount}/.vastclaw && "
                                        f'printf "%s" "$SEED_JSON" > {mount}/.vastclaw/run-context.json'
                                    ),
                                ],
                                volume_mounts=[
                                    client.V1VolumeMount(name="data", mount_path=mount)
                                ],
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="data",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=workflow_id
                                ),
                            )
                        ],
                    )
                ),
            ),
        )
        batch = client.BatchV1Api()
        try:
            batch.create_namespaced_job(namespace=namespace, body=body)
        except client.exceptions.ApiException as exc:
            if exc.status != 409:
                raise
            batch.delete_namespaced_job(
                name=job_name, namespace=namespace, propagation_policy="Background"
            )
            time.sleep(1)
            batch.create_namespaced_job(namespace=namespace, body=body)
        self._wait_job(namespace, job_name)

    def create_volume_snapshot(
        self,
        department: str,
        workflow_id: str,
        pvc_name: str,
        *,
        run_id: str = "",
        label: str = "",
        name_hint: str = "",
    ) -> str:
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        hint = re.sub(r"[^a-z0-9-]+", "-", (name_hint or label or "chk").lower()).strip("-")[
            :18
        ] or "chk"
        labels = {
            "vastclaw.vastdata.com/department": department,
            "vastclaw.vastdata.com/workflow": workflow_id,
            "vastclaw.vastdata.com/source-pvc": pvc_name,
        }
        if run_id:
            labels["vastclaw.vastdata.com/run"] = run_id
        if label:
            labels["vastclaw.vastdata.com/label"] = label[:63]

        attempts = max(1, self._settings.snapshot_max_retries)
        backoff = max(0.5, self._settings.snapshot_retry_backoff_seconds)
        last_error = ""
        for attempt in range(attempts):
            uniq = uuid.uuid4().hex[:6]
            snap_name = f"snap-{workflow_id}-{hint}-{uniq}"[:63].rstrip("-")
            body = {
                "apiVersion": f"{self._SNAPSHOT_GROUP}/{self._SNAPSHOT_VERSION}",
                "kind": "VolumeSnapshot",
                "metadata": {
                    "name": snap_name,
                    "namespace": namespace,
                    "labels": labels,
                },
                "spec": {
                    "volumeSnapshotClassName": self._snapshot_class_name(),
                    "source": {"persistentVolumeClaimName": pvc_name},
                },
            }
            custom = client.CustomObjectsApi()
            with self._snapshot_slots:
                try:
                    custom.create_namespaced_custom_object(
                        group=self._SNAPSHOT_GROUP,
                        version=self._SNAPSHOT_VERSION,
                        namespace=namespace,
                        plural=self._SNAPSHOT_PLURAL,
                        body=body,
                    )
                except client.exceptions.ApiException as exc:
                    raise RuntimeError(
                        "VolumeSnapshot create failed (is external-snapshotter "
                        f"installed?): {exc.body}"
                    ) from exc
                try:
                    self._wait_volume_snapshot_ready(
                        namespace,
                        snap_name,
                        timeout=self._settings.snapshot_ready_timeout_seconds,
                    )
                    return snap_name
                except _TransientSnapshotError as exc:
                    last_error = str(exc)
                except Exception:
                    self._delete_volume_snapshot(namespace, snap_name)
                    raise
            self._delete_volume_snapshot(namespace, snap_name)
            if attempt < attempts - 1:
                delay = min(backoff * (2**attempt), 60.0)
                _log.warning(
                    "snapshot of %s rejected by VMS (%s); retrying in %.0fs "
                    "(attempt %d/%d)",
                    pvc_name,
                    last_error,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)
        raise RuntimeError(
            f"VolumeSnapshot of {pvc_name} failed after {attempts} attempts: {last_error}"
        )

    def _wait_volume_snapshot_ready(
        self, namespace: str, name: str, timeout: float = 180.0
    ) -> None:
        """Block until the snapshot is usable."""
        from kubernetes import client

        custom = client.CustomObjectsApi()
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            obj = custom.get_namespaced_custom_object(
                group=self._SNAPSHOT_GROUP,
                version=self._SNAPSHOT_VERSION,
                namespace=namespace,
                plural=self._SNAPSHOT_PLURAL,
                name=name,
            )
            status = obj.get("status") or {}
            if status.get("readyToUse"):
                return
            message = (status.get("error") or {}).get("message") or ""
            if message:
                last_error = message
                if not _is_transient_snapshot_error(message):
                    raise RuntimeError(message)
            time.sleep(2)
        raise _TransientSnapshotError(
            last_error or f"VolumeSnapshot {name} not ready after {timeout}s"
        )

    def _delete_volume_snapshot(self, namespace: str, name: str) -> None:
        from kubernetes import client

        try:
            client.CustomObjectsApi().delete_namespaced_custom_object(
                group=self._SNAPSHOT_GROUP,
                version=self._SNAPSHOT_VERSION,
                namespace=namespace,
                plural=self._SNAPSHOT_PLURAL,
                name=name,
            )
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                _log.warning("could not delete failed snapshot %s: %s", name, exc)

    def _wait_job(self, namespace: str, name: str, timeout: float = 120.0) -> None:
        from kubernetes import client

        batch = client.BatchV1Api()
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = batch.read_namespaced_job(name=name, namespace=namespace)
            if job.status and job.status.succeeded:
                return
            if job.status and job.status.failed:
                raise RuntimeError(f"seed job {name} failed")
            time.sleep(2)
        raise TimeoutError(f"seed job {name} not finished after {timeout}s")

    def list_volume_snapshots(self, department: str, workflow_id: str) -> list[dict]:
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        selector = f"vastclaw.vastdata.com/workflow={workflow_id}"
        custom = client.CustomObjectsApi()
        try:
            resp = custom.list_namespaced_custom_object(
                group=self._SNAPSHOT_GROUP,
                version=self._SNAPSHOT_VERSION,
                namespace=namespace,
                plural=self._SNAPSHOT_PLURAL,
                label_selector=selector,
            )
        except client.exceptions.ApiException as exc:
            if exc.status in (404, 403):
                return []
            raise
        out: list[dict] = []
        for item in resp.get("items", []):
            meta = item.get("metadata") or {}
            status = item.get("status") or {}
            labels = meta.get("labels") or {}
            source = (item.get("spec") or {}).get("source") or {}
            out.append(
                {
                    "name": meta.get("name", ""),
                    "source_pvc": source.get("persistentVolumeClaimName", ""),
                    "ready": bool(status.get("readyToUse")),
                    "created_at": meta.get("creationTimestamp", ""),
                    "run_id": labels.get("vastclaw.vastdata.com/run", ""),
                    "snapshot_class": (item.get("spec") or {}).get(
                        "volumeSnapshotClassName", ""
                    ),
                }
            )
        return sorted(out, key=lambda s: s.get("created_at", ""), reverse=True)

    def restore_pvc_from_snapshot(
        self,
        department: str,
        workflow_id: str,
        snapshot_name: str,
        pvc_name: str,
        *,
        subagent: str = "",
    ) -> str:
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        if not pvc_name:
            suffix = subagent or "restored"
            pvc_name = f"{workflow_id}-{suffix}"[:63].rstrip("-")
        labels = {
            "vastclaw.vastdata.com/department": department,
            "vastclaw.vastdata.com/workflow": workflow_id,
            "vastclaw.vastdata.com/restore-source": snapshot_name,
        }
        role = "subagent" if subagent else "restore"
        labels["vastclaw.vastdata.com/role"] = role
        if subagent:
            labels["vastclaw.vastdata.com/subagent"] = subagent
        elif pvc_name == workflow_id:
            role = "head"
            labels["vastclaw.vastdata.com/role"] = role
        api = client.CoreV1Api()
        body = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                labels=labels,
                annotations={
                    "vastclaw.vastdata.com/restore-source": snapshot_name,
                },
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=[self._settings.access_mode],
                storage_class_name=(self._settings.storage_class or namespace),
                resources=_pvc_resources(client, self._settings.storage_request),
                data_source=client.V1TypedLocalObjectReference(
                    name=snapshot_name,
                    kind="VolumeSnapshot",
                    api_group=self._SNAPSHOT_GROUP,
                ),
            ),
        )
        try:
            api.create_namespaced_persistent_volume_claim(namespace=namespace, body=body)
        except client.exceptions.ApiException as exc:
            if exc.status != 409:
                raise
        return pvc_name

    def sandboxes_alive(self, department: str, workflow_id: str) -> bool:
        """True if any sandbox pod for this workflow is still Running (keep-alive window)."""
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        api = client.CoreV1Api()
        try:
            pods = api.list_namespaced_pod(namespace=namespace).items
        except client.exceptions.ApiException as exc:
            if exc.status in (404, 403):
                return False
            raise
        prefix = f"{workflow_id}-"
        for pod in pods:
            name = (pod.metadata.name if pod.metadata else "") or ""
            if not name.startswith(prefix):
                continue
            phase = (pod.status.phase if pod.status else "") or ""
            if phase == "Running":
                return True
        return False

    def wake_for_continue(self, department: str, workflow_id: str) -> dict:
        """Prepare storage for a follow-up turn after sandbox pods have died."""
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        api = client.CoreV1Api()
        selector = f"vastclaw.vastdata.com/workflow={workflow_id}"
        existing = {
            item.metadata.name
            for item in api.list_namespaced_persistent_volume_claim(
                namespace=namespace, label_selector=selector
            ).items
        }
        snapshots = self.list_volume_snapshots(department, workflow_id)
        latest_by_pvc: dict[str, dict] = {}
        for snap in snapshots:
            if not snap.get("ready"):
                continue
            src = snap.get("source_pvc") or ""
            if src and src not in latest_by_pvc:
                latest_by_pvc[src] = snap

        actions: list[dict[str, str]] = []
        head_action = "missing"
        head_snapshot = ""

        if workflow_id in existing:
            head_action = "remount"
            actions.append(
                {
                    "pvc": workflow_id,
                    "role": "head",
                    "action": "remount",
                    "snapshot": "",
                }
            )
        elif workflow_id in latest_by_pvc:
            snap = latest_by_pvc[workflow_id]
            self.restore_pvc_from_snapshot(
                department, workflow_id, snap["name"], workflow_id
            )
            head_action = "restored_from_snapshot"
            head_snapshot = snap["name"]
            actions.append(
                {
                    "pvc": workflow_id,
                    "role": "head",
                    "action": "restored_from_snapshot",
                    "snapshot": snap["name"],
                }
            )
        else:
            # Last resort: empty head with the same workflow id (still not a new id).
            self.ensure_workflow_sandbox(department, workflow_id)
            head_action = "created_empty"
            actions.append(
                {
                    "pvc": workflow_id,
                    "role": "head",
                    "action": "created_empty",
                    "snapshot": "",
                }
            )

        for src, snap in latest_by_pvc.items():
            if src == workflow_id:
                continue
            if src in existing:
                actions.append(
                    {
                        "pvc": src,
                        "role": "subagent",
                        "action": "remount",
                        "snapshot": "",
                    }
                )
                continue
            # Sub-agent slug is the PVC suffix after "<workflow_id>-"
            prefix = f"{workflow_id}-"
            sub = src[len(prefix) :] if src.startswith(prefix) else ""
            self.restore_pvc_from_snapshot(
                department, workflow_id, snap["name"], src, subagent=sub
            )
            actions.append(
                {
                    "pvc": src,
                    "role": "subagent",
                    "action": "restored_from_snapshot",
                    "snapshot": snap["name"],
                }
            )

        # Surviving PVCs that had no snapshot entry (still remountable).
        for name in sorted(existing):
            if any(a["pvc"] == name for a in actions):
                continue
            role = "head" if name == workflow_id else "subagent"
            actions.append(
                {"pvc": name, "role": role, "action": "remount", "snapshot": ""}
            )

        return {
            "woke": True,
            "head_action": head_action,
            "head_snapshot": head_snapshot,
            "actions": actions,
        }

    def get_storage_state(self, department: str, workflow_id: str) -> dict:
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        api = client.CoreV1Api()
        selector = f"vastclaw.vastdata.com/workflow={workflow_id}"
        pvcs = api.list_namespaced_persistent_volume_claim(
            namespace=namespace, label_selector=selector
        )
        pvc_rows: list[dict] = []
        for item in pvcs.items:
            meta = item.metadata
            spec = item.spec
            status = item.status
            labels = meta.labels or {}
            annotations = meta.annotations or {}
            clone_source = (
                labels.get("vastclaw.vastdata.com/clone-source")
                or annotations.get("vastclaw.vastdata.com/clone-source")
                or ""
            )
            if not clone_source and spec and spec.data_source:
                if spec.data_source.kind == "PersistentVolumeClaim":
                    clone_source = spec.data_source.name or ""
            role = labels.get("vastclaw.vastdata.com/role", "")
            if not role:
                role = "head" if meta.name == workflow_id else "subagent"
            cap = ""
            if status and status.capacity and status.capacity.get("storage"):
                cap = status.capacity["storage"]
            pvc_rows.append(
                {
                    "name": meta.name,
                    "role": role,
                    "subagent": labels.get("vastclaw.vastdata.com/subagent", ""),
                    "clone_source_pvc": clone_source,
                    "storage_class": (spec.storage_class_name if spec else "") or "",
                    "access_mode": (spec.access_modes[0] if spec and spec.access_modes else ""),
                    "status": (status.phase if status else "") or "",
                    "capacity": cap,
                }
            )
        snapshots = self.list_volume_snapshots(department, workflow_id)
        last_checkpoint = snapshots[0]["name"] if snapshots else ""
        return {
            "workflow_id": workflow_id,
            "department": department,
            "head_pvc": workflow_id,
            "pvcs": sorted(pvc_rows, key=lambda r: r["name"]),
            "snapshots": snapshots,
            "clone_enabled": self._settings.clone_subagents_from_head,
            "checkpoint_enabled": self._settings.checkpoint_before_fanout,
            "checkpoint_on_subagent_complete": self._settings.checkpoint_on_subagent_complete,
            "wake_on_continue": self._settings.wake_on_continue,
            "snapshot_class": self._snapshot_class_name(),
            "last_checkpoint": last_checkpoint,
        }

    def list_workflow_pvcs(self, department: str, workflow_id: str) -> list[str]:
        """All PVCs (head + sub-agents) for a workflow, matched by the workflow
        label so the whole hierarchy is deleted together."""
        from kubernetes import client, config

        if self._settings.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        namespace = f"vastclaw-{department}"
        api = client.CoreV1Api()
        selector = f"vastclaw.vastdata.com/workflow={workflow_id}"
        pvcs = api.list_namespaced_persistent_volume_claim(
            namespace=namespace, label_selector=selector
        )
        return sorted(item.metadata.name for item in pvcs.items)

    def _reap(self, api: object, namespace: str, items: list) -> list[str]:
        """Delete the given PVCs *and* their bound PVs."""
        from kubernetes import client

        pv_names = [
            it.spec.volume_name for it in items if it.spec and getattr(it.spec, "volume_name", None)
        ]
        names = sorted(it.metadata.name for it in items)
        for name in names:
            try:
                api.delete_namespaced_persistent_volume_claim(name=name, namespace=namespace)
            except client.exceptions.ApiException as exc:
                if exc.status != 404:  # already gone -> fine
                    raise
        for pv in pv_names:
            try:
                api.delete_persistent_volume(name=pv)
            except client.exceptions.ApiException as exc:
                if exc.status != 404:  # already gone / dynamically reclaimed -> fine
                    raise
        return names

    def delete_workflow_pvcs(self, department: str, workflow_id: str) -> list[str]:
        from kubernetes import client, config

        if self._settings.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        namespace = f"vastclaw-{department}"
        api = client.CoreV1Api()
        selector = f"vastclaw.vastdata.com/workflow={workflow_id}"
        pvcs = api.list_namespaced_persistent_volume_claim(
            namespace=namespace, label_selector=selector
        )
        return self._reap(api, namespace, list(pvcs.items))

    def _sweep_department_pvs(self, api: object, department: str) -> list[str]:
        from kubernetes import client

        namespace = f"vastclaw-{department}"
        try:
            pvs = api.list_persistent_volume()
        except client.exceptions.ApiException:
            return []
        victims: list[str] = []
        for pv in pvs.items:
            spec = getattr(pv, "spec", None)
            status = getattr(pv, "status", None)
            phase = getattr(status, "phase", None)
            if phase not in ("Released", "Available"):
                continue
            sc = getattr(spec, "storage_class_name", None)
            claim = getattr(spec, "claim_ref", None)
            claim_ns = getattr(claim, "namespace", None)
            if sc == namespace or claim_ns == namespace:
                victims.append(pv.metadata.name)
        for name in victims:
            try:
                api.delete_persistent_volume(name=name)
            except client.exceptions.ApiException as exc:
                if exc.status != 404:
                    raise
        return victims

    def delete_department_pvcs(self, department: str) -> list[str]:
        from kubernetes import client, config

        if self._settings.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()
        namespace = f"vastclaw-{department}"
        api = client.CoreV1Api()
        # Label-key existence selector -> matches head + sub-agent PVCs of any workflow.
        pvcs = api.list_namespaced_persistent_volume_claim(
            namespace=namespace, label_selector="vastclaw.vastdata.com/workflow"
        )
        names = self._reap(api, namespace, list(pvcs.items))
        self._sweep_department_pvs(api, department)
        return names

    def delete_workflow_resources(
        self, department: str, workflow_id: str, *, agent_sandbox: AgentSandboxSettings
    ) -> dict[str, list[str]]:
        from kubernetes import client

        self._load_kube()
        namespace = f"vastclaw-{department}"
        core = client.CoreV1Api()
        batch = client.BatchV1Api()
        custom = client.CustomObjectsApi()
        out: dict[str, list[str]] = {
            "sandboxes": [],
            "jobs": [],
            "snapshots": [],
            "pvcs": [],
        }

        # 1) Agent-Sandbox CRs (+ their pods) — matched by name prefix.
        prefix = f"{workflow_id}-"
        try:
            listed = custom.list_namespaced_custom_object(
                group=agent_sandbox.api_group,
                version=agent_sandbox.api_version,
                namespace=namespace,
                plural=agent_sandbox.plural,
            )
        except client.exceptions.ApiException as exc:
            if exc.status not in (404, 403):
                raise
            listed = {"items": []}
        for item in listed.get("items", []):
            name = (item.get("metadata") or {}).get("name") or ""
            if name != workflow_id and not name.startswith(prefix):
                continue
            try:
                custom.delete_namespaced_custom_object(
                    group=agent_sandbox.api_group,
                    version=agent_sandbox.api_version,
                    namespace=namespace,
                    plural=agent_sandbox.plural,
                    name=name,
                )
                out["sandboxes"].append(name)
            except client.exceptions.ApiException as exc:
                if exc.status != 404:
                    raise
            # The pod shares the CR name; drop it so the RWO PVC is free to reap.
            try:
                core.delete_namespaced_pod(
                    name=name,
                    namespace=namespace,
                    grace_period_seconds=0,
                    propagation_policy="Background",
                )
            except client.exceptions.ApiException as exc:
                if exc.status != 404:
                    raise

        # 2) Jobs — backend-created, carry our workflow label.
        try:
            jobs = batch.list_namespaced_job(
                namespace=namespace,
                label_selector=f"vastclaw.vastdata.com/workflow={workflow_id}",
            )
            for job in jobs.items:
                name = job.metadata.name
                try:
                    batch.delete_namespaced_job(
                        name=name, namespace=namespace, propagation_policy="Background"
                    )
                    out["jobs"].append(name)
                except client.exceptions.ApiException as exc:
                    if exc.status != 404:
                        raise
        except client.exceptions.ApiException as exc:
            if exc.status not in (404, 403):
                raise

        # 3) VolumeSnapshot checkpoints — reuse the labelled listing + delete helper.
        for snap in self.list_volume_snapshots(department, workflow_id):
            name = snap.get("name") or ""
            if not name:
                continue
            self._delete_volume_snapshot(namespace, name)
            out["snapshots"].append(name)

        # 4) PVCs (head + sub-agent clones) and their bound PVs.
        out["pvcs"] = self.delete_workflow_pvcs(department, workflow_id)
        return out

    def purge_department(
        self,
        department: str,
        *,
        agent_sandbox: AgentSandboxSettings,
        runner_secrets: tuple[str, ...] = (_RUNNER_SECRET,),
    ) -> dict[str, list[str]]:
        from kubernetes import client, config

        if self._settings.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()

        namespace = f"vastclaw-{department}"
        core = client.CoreV1Api()
        batch = client.BatchV1Api()
        custom = client.CustomObjectsApi()
        out: dict[str, list[str]] = {
            "sandboxes": [],
            "jobs": [],
            "pods": [],
            "pvcs": [],
            "pvs": [],
            "secrets": [],
        }

        try:
            sandboxes = custom.list_namespaced_custom_object(
                group=agent_sandbox.api_group,
                version=agent_sandbox.api_version,
                namespace=namespace,
                plural=agent_sandbox.plural,
            )
            for item in sandboxes.get("items", []):
                name = item["metadata"]["name"]
                try:
                    custom.delete_namespaced_custom_object(
                        group=agent_sandbox.api_group,
                        version=agent_sandbox.api_version,
                        namespace=namespace,
                        plural=agent_sandbox.plural,
                        name=name,
                    )
                    out["sandboxes"].append(name)
                except client.exceptions.ApiException as exc:
                    if exc.status != 404:
                        raise
        except client.exceptions.ApiException as exc:
            if exc.status not in (404, 403):
                raise

        try:
            jobs = batch.list_namespaced_job(
                namespace=namespace, label_selector="vastclaw.vastdata.com/workflow"
            )
            for job in jobs.items:
                name = job.metadata.name
                try:
                    batch.delete_namespaced_job(
                        name=name,
                        namespace=namespace,
                        propagation_policy="Background",
                    )
                    out["jobs"].append(name)
                except client.exceptions.ApiException as exc:
                    if exc.status != 404:
                        raise
        except client.exceptions.ApiException as exc:
            if exc.status not in (404, 403):
                raise

        try:
            core.delete_collection_namespaced_pod(
                namespace=namespace,
                label_selector="vastclaw.vastdata.com/workflow",
                grace_period_seconds=0,
                propagation_policy="Background",
            )
            out["pods"].append("(collection vastclaw.vastdata.com/workflow)")
        except client.exceptions.ApiException as exc:
            if exc.status not in (404, 403):
                raise

        try:
            pvcs = core.list_namespaced_persistent_volume_claim(namespace=namespace)
            out["pvcs"] = self._reap(core, namespace, list(pvcs.items))
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise

        out["pvs"] = self._sweep_department_pvs(core, department)

        for secret_name in runner_secrets:
            try:
                core.delete_namespaced_secret(name=secret_name, namespace=namespace)
                out["secrets"].append(secret_name)
            except client.exceptions.ApiException as exc:
                if exc.status != 404:
                    raise

        return out


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class _Subagent(BaseModel):
    name: str
    tool: str
    focus: str  # what this sub-agent was asked to investigate (its input)
    data: str  # the data it produced (rendered in the node inspector / artifacts)


_SUBAGENTS: list[_Subagent] = [
    _Subagent(
        name="competitor-mentions",
        tool="transcript_search",
        focus="Find competitors named or alluded to across the call and how they came up.",
        data=(
            "6 competitor / peer mentions retrieved from the earnings-call corpus:\n"
            "| peer | turns | sentiment | context |\n"
            "| Siemens | 3 | competitive | 'industrial orders under pressure vs peers' |\n"
            "| Honeywell | 2 | neutral | evaluated as adjacent industrial |\n"
            "| Raytheon | 1 | positive (us) | aviation recovery lagging peers |"
        ),
    ),
    _Subagent(
        name="call-scoring",
        tool="structured_query",
        focus="Score the earnings narrative for account urgency (demand, risk, timing).",
        data=(
            "Call scored from snippet-level signals:\n"
            "- Demand: mixed (industrial soft, aviation recovering)\n"
            "- Competitive pressure: elevated vs peers\n"
            "- Guidance risk: present in Q&A\n"
            "- Timing: next-quarter catalysts — score 78/100"
        ),
    ),
    _Subagent(
        name="similar-conversations",
        tool="similar_calls",
        focus="Retrieve prior earnings calls with the closest trajectory to ground the recommendation.",
        data=(
            "4 similar earnings calls surfaced via array_cosine_distance over call embeddings:\n"
            "- 3 of 4 Conglomerate/Industrial peers with order-book softness\n"
            "- Common winning move: reframe around services attach + aviation recovery\n"
            "- Median time-to-follow-up after this stage: 21 days"
        ),
    ),
]


QA_SPAN_KIND = "qa"


def _qa_report_context(spans: list[Span], report_run_id: str, title: str) -> str:
    if not report_run_id:
        return ""
    summary = ""
    recommendation = ""
    findings: list[tuple[str, str]] = []
    for s in spans:
        if s.run_id != report_run_id:
            continue
        if s.attributes.get("summary"):
            summary = s.attributes.get("summary", "")
            recommendation = s.attributes.get("recommendation", "") or recommendation
        elif s.kind == "agent" and s.agent_name != "research-lead":
            text = (s.attributes.get("data_produced") or "").strip()
            if text:
                findings.append((s.agent_name, text))
    parts: list[str] = []
    if summary:
        parts.append(f"# Existing report: {title}\n\n{summary}")
    if recommendation:
        parts.append(f"## Prior recommendation\n{recommendation}")
    if findings:
        block = ["## Sub-agent findings (evidence behind the report)"]
        for name, text in findings:
            block.append(f"### {name}\n{text}")
        parts.append("\n".join(block))
    return "\n\n".join(parts).strip()


def _qa_prior_thread(spans: list[Span], workflow_id: str, current_run_id: str) -> str:
    """Earlier completed follow-up Q&A in this workflow, oldest-first, for chat memory."""
    msgs: list[tuple[str, str, str]] = []
    for s in spans:
        if s.workflow_id != workflow_id or s.kind != QA_SPAN_KIND:
            continue
        if s.run_id == current_run_id or not s.end_time:
            continue
        q = (s.attributes.get("question") or "").strip()
        a = (s.attributes.get("answer") or "").strip()
        if q and a:
            msgs.append((s.start_time, q, a))
    if not msgs:
        return ""
    msgs.sort(key=lambda m: m[0])
    lines = ["## Earlier follow-up questions in this thread"]
    for _, q, a in msgs[-6:]:
        lines.append(f"Q: {q}\nA: {a}")
    return "\n\n".join(lines)


def _qa_grounding(
    settings: Settings, department: str, question: str
) -> tuple[str, list[dict]]:
    if not settings.mcp.enabled:
        return "", []
    try:
        from vastclaw_ui.agent_runner import _ground_subagent
        from vastclaw_ui.rag_client import RagMcpClient

        client = RagMcpClient(settings.mcp, department)
        grounding, evidence, _status, _tokens, _filters = _ground_subagent(
            settings, department, question, client
        )
        return grounding, evidence
    except Exception as exc:  # noqa: BLE001 — retrieval must never break the answer
        _log.warning("QA grounding failed for %s: %s", department, exc)
        return "", []


def _qa_system_prompt(settings: Settings, department: str) -> str:
    persona = settings.persona(department).strip()
    base = (
        "You are answering a FOLLOW-UP QUESTION about a research report you already "
        "produced for this workflow. Do NOT start a new investigation or propose a "
        "plan of sub-agents. Answer only from the report, the sub-agent findings and "
        "the retrieved evidence provided below. Be concise and specific; cite "
        "companies / quarters / sources where relevant. If the answer is not supported "
        "by the material, say so plainly instead of speculating. Respond in clear "
        "markdown prose (no JSON)."
    )
    return f"{persona}\n\n{base}" if persona else base


def _qa_fallback(context: str, question: str) -> str:
    del question
    head = (
        "_A grounded answer could not be generated right now (the model is offline or "
        "returned no content). Based on the existing report:_"
    )
    excerpt = context.strip()
    if len(excerpt) > 1200:
        excerpt = excerpt[:1200].rstrip() + "…"
    if not excerpt:
        excerpt = "_No prior report content is available for this workflow yet._"
    return f"{head}\n\n{excerpt}"


def _begin_qa(
    settings: Settings, spans: SpanSource, history: HistoryStore, request: RunRequest
) -> None:
    """Write the QA running span (question only) before the model call."""
    dept = request.department
    nim = NimClient(settings.model)
    model_label = settings.model.model if nim.enabled else "offline"
    attrs_base = department_span_attrs(settings, dept)
    spans.write_spans(
        [
            Span(
                trace_id=request.run_id,
                span_id=f"{request.run_id}-qa",
                parent_span_id="",
                workflow_id=request.workflow_id,
                run_id=request.run_id,
                name="Follow-up Q&A",
                kind=QA_SPAN_KIND,
                agent_name="research-lead",
                department=dept,
                user=request.user,
                start_time=_now(),
                end_time="",
                status="running",
                attributes={
                    **attrs_base,
                    "mode": "qa",
                    "phase": "qa",
                    "turn_id": request.turn_id,
                    "question": request.prompt,
                    "produced_by": model_label,
                },
            )
        ]
    )
    if request.turn_id:
        history.update_turn_status(request.workflow_id, request.turn_id, "running")


def _run_qa(
    settings: Settings, spans: SpanSource, history: HistoryStore, request: RunRequest
) -> None:
    """Ground and answer a Quick Q&A turn; updates the span written by ``_begin_qa``."""
    nim = NimClient(settings.model)
    dept = request.department
    qa_id = f"{request.run_id}-qa"
    model_label = settings.model.model if nim.enabled else "offline"

    wf_title = ""
    report_run_id = ""
    try:
        wf = history.get_workflow(request.workflow_id, None)
        wf_title = wf.title
        report_run_id = wf.last_run_id or ""
    except Exception:  # noqa: BLE001 — answer degraded, don't abort
        _log.warning("QA: could not load workflow %s", request.workflow_id, exc_info=True)

    all_spans = spans.fetch(dept)
    # Seed the running span if begin_question was skipped.
    if not any(s.span_id == qa_id for s in all_spans):
        _begin_qa(settings, spans, history, request)
    context = _qa_report_context(all_spans, report_run_id, wf_title)
    prior = _qa_prior_thread(all_spans, request.workflow_id, request.run_id)
    attrs_base = department_span_attrs(settings, dept)

    degraded = False
    citations: list[dict] = []
    answer = ""
    try:
        grounding, citations = _qa_grounding(settings, dept, request.prompt)
        if not nim.enabled:
            raise ModelError("model disabled")
        user_parts: list[str] = []
        if context:
            user_parts.append(context)
        if prior:
            user_parts.append(prior)
        user_parts.append(
            "## Retrieved evidence for this question\n"
            + (grounding or "(no additional corpus snippets retrieved)")
        )
        user_parts.append(f"## Follow-up question\n{request.prompt}")
        completion = nim.complete(
            _qa_system_prompt(settings, dept),
            "\n\n".join(user_parts),
            max_tokens=_budget(settings, 900),
        )
        answer = _strip_think(completion.text).strip()
        if not answer:
            raise ModelError("model returned no content")
    except Exception as exc:  # noqa: BLE001 — degrade to a report-based answer
        _log.warning("QA answer failed for run %s: %s", request.run_id, exc)
        degraded = True
        answer = _qa_fallback(context, request.prompt)

    done = _now()
    spans.update_span(
        qa_id,
        end_time=done,
        status="degraded" if degraded else "ok",
        attributes={
            **attrs_base,
            "mode": "qa",
            "phase": "qa",
            "turn_id": request.turn_id,
            "question": request.prompt,
            "answer": answer,
            "citations_json": json.dumps(citations),
            "produced_by": "fallback" if degraded else model_label,
            "model": "fallback" if degraded else model_label,
            "qa_degraded": "true" if degraded else "",
        },
    )
    if request.turn_id:
        history.update_turn_status(request.workflow_id, request.turn_id, "completed")
    history.update_run_status(request.run_id, "completed", done)


class _QaCapable:
    """Mixin: Quick Q&A helpers for a runtime (needs ``_settings``, ``_spans``, ``_history``)."""

    _settings: Settings
    _spans: SpanSource
    _history: HistoryStore

    def begin_question(self, request: RunRequest) -> None:
        _begin_qa(self._settings, self._spans, self._history, request)

    def answer_question(self, request: RunRequest) -> None:
        threading.Thread(target=self._safe_answer, args=(request,), daemon=True).start()

    def _safe_answer(self, request: RunRequest) -> None:
        try:
            _run_qa(self._settings, self._spans, self._history, request)
        except Exception:  # a background answer must never take down the process
            _log.exception("follow-up Q&A run %s failed", request.run_id)
            self._fail_qa(request)

    def _fail_qa(self, request: RunRequest) -> None:
        now = _now()
        try:
            self._spans.update_span(f"{request.run_id}-qa", end_time=now, status="error")
            if request.turn_id:
                self._history.update_turn_status(
                    request.workflow_id, request.turn_id, "error"
                )
            self._history.update_run_status(request.run_id, "error", now)
        except Exception:
            _log.exception("failed to record QA error for run %s", request.run_id)


class LocalResearchRuntime(_QaCapable):
    """Runs a real (deterministic) multi-agent research flow in-process."""

    def __init__(
        self,
        spans: SpanSource,
        history: HistoryStore,
        settings: Settings | None = None,
        step_delay: float = 1.2,
    ) -> None:
        self._spans = spans
        self._history = history
        self._settings = settings or Settings()
        self._step_delay = step_delay

    def start_run(self, request: RunRequest) -> None:
        thread = threading.Thread(target=self._safe_execute, args=(request,), daemon=True)
        thread.start()

    def _safe_execute(self, request: RunRequest) -> None:
        try:
            self._execute(request)
        except Exception:  # a background run must never take down the process
            _log.exception("research run %s failed", request.run_id)
            self._fail(request)

    def _fail(self, request: RunRequest) -> None:
        now = _now()
        try:
            self._spans.finalize_span(f"{request.run_id}-root", now, "error")
            if request.turn_id:
                self._history.update_turn_status(request.workflow_id, request.turn_id, "error")
            self._history.update_workflow_status(request.workflow_id, "error", now)
            self._history.update_run_status(request.run_id, "error", now)
        except Exception:
            _log.exception("failed to record error status for run %s", request.run_id)

    def _span(
        self,
        request: RunRequest,
        span_id: str,
        parent: str,
        name: str,
        kind: str,
        agent: str,
        attributes: dict[str, str],
        status: str = "ok",
    ) -> Span:
        start = _now()
        attrs = {**department_span_attrs(self._settings, request.department), **attributes}
        # Estimated token accounting across the node's textual input + output.
        body = attrs.get("output", "") or attrs.get("data_produced", "") or attrs.get("log", "")
        attrs.setdefault("tokens_prompt", str(_est_tokens(attrs.get("input", "") + request.prompt)))
        attrs.setdefault("tokens_completion", str(_est_tokens(body)))
        if status != "running":
            attrs.setdefault("duration_ms", str(int(self._step_delay * 1000)))
        return Span(
            trace_id=request.run_id,
            span_id=span_id,
            parent_span_id=parent,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            name=name,
            kind=kind,
            agent_name=agent,
            department=request.department,
            user=request.user,
            start_time=start,
            end_time="" if status == "running" else start,
            status=status,
            attributes=attrs,
        )

    def _execute(self, request: RunRequest) -> None:
        now = _now()
        if request.turn_id:
            self._history.update_turn_status(request.workflow_id, request.turn_id, "running")
        self._history.update_workflow_status(request.workflow_id, "running", now)

        root_id = f"{request.run_id}-root"
        vocab = span_vocab(self._settings, request.department)
        plan_items = _fallback_plan(self._settings, request.department)
        # Root written running (no end_time) so the DAG shows a live coordinator.
        self._spans.write_spans(
            [
                self._span(
                    request,
                    root_id,
                    "",
                    vocab["lead_title"],
                    "agent",
                    "research-lead",
                    {
                        "phase": "initialize",
                        "log": vocab["lead_log"],
                        "input": request.prompt,
                        "sandbox_pvc": request.sandbox.pvc_name,
                        "sandbox_view": request.sandbox.view_path,
                    },
                    status="running",
                )
            ]
        )

        time.sleep(self._step_delay)
        self._spans.write_spans(
            [
                self._span(
                    request,
                    f"{root_id}-plan",
                    root_id,
                    vocab["plan_title"],
                    "internal",
                    "research-lead",
                    {
                        "phase": "initialize",
                        "input": request.prompt,
                        "decision": (
                            f"Decomposed into {len(plan_items)} "
                            f"{vocab['plan_decision_suffix']}."
                        ),
                        "output": "\n".join(
                            f"{i + 1}. {s.focus}" for i, s in enumerate(plan_items)
                        ),
                        "plan_json": json.dumps(
                            [
                                {"name": s.name, "focus": s.focus, "rationale": s.rationale}
                                for s in plan_items
                            ]
                        ),
                    },
                )
            ]
        )

        # Human-in-the-loop gate: pause after proposing the plan (see KubernetesRuntime).
        if self._settings.runtime.require_plan_approval and not request.auto_approve_plan:
            paused = _now()
            self._spans.write_spans(
                [
                    self._span(
                        request,
                        f"{root_id}-await",
                        root_id,
                        vocab["await_title"],
                        "internal",
                        "research-lead",
                        {
                            "phase": "initialize",
                            "log": "Waiting for a human to review and approve the plan.",
                            "decision": "Plan proposed — awaiting approval.",
                        },
                        status="running",
                    )
                ]
            )
            if request.turn_id:
                self._history.update_turn_status(
                    request.workflow_id, request.turn_id, "awaiting_approval"
                )
            self._history.update_workflow_status(request.workflow_id, "awaiting_approval", paused)
            self._history.update_run_status(request.run_id, "awaiting_approval", "")
            return

        self._run_fanout(request, root_id)

    def resume_run(self, request: RunRequest, plan: list[dict[str, str]]) -> None:
        threading.Thread(
            target=self._safe_resume, args=(request,), daemon=True
        ).start()

    def _safe_resume(self, request: RunRequest) -> None:
        try:
            self._resume(request)
        except Exception:
            _log.exception("research run %s failed on resume", request.run_id)
            self._fail(request)

    def _resume(self, request: RunRequest) -> None:
        now = _now()
        root_id = f"{request.run_id}-root"
        if request.turn_id:
            self._history.update_turn_status(request.workflow_id, request.turn_id, "running")
        self._history.update_workflow_status(request.workflow_id, "running", now)
        self._history.update_run_status(request.run_id, "running", "")
        self._spans.finalize_span(f"{root_id}-await", now, "ok")
        self._run_fanout(request, root_id)

    def _run_fanout(self, request: RunRequest, root_id: str) -> None:
        vocab = span_vocab(self._settings, request.department)
        plan_items = _fallback_plan(self._settings, request.department)
        canned = {s.name: s for s in _SUBAGENTS}
        for item in plan_items:
            time.sleep(self._step_delay)
            sub = canned.get(item.name)
            data = sub.data if sub else f"_(local demo)_ Focus: {item.focus}"
            tool = sub.tool if sub else vocab["primary_search_tool"]
            sub_id = f"{root_id}-{item.name}"
            self._spans.write_spans(
                [
                    self._span(
                        request,
                        sub_id,
                        root_id,
                        item.name,
                        "agent",
                        item.name,
                        {
                            "phase": "research",
                            "input": item.focus,
                            "log": (
                                f"Spawned {item.name} ({vocab['role_title']}) with an "
                                "isolated child sandbox view."
                            ),
                            "decision": f"Investigate: {item.focus}",
                            "data_produced": data,
                        },
                    ),
                    self._span(
                        request,
                        f"{sub_id}-rag",
                        sub_id,
                        vocab["search_title"],
                        "tool",
                        item.name,
                        {
                            "phase": "retrieval",
                            "tool": tool,
                            "input": item.focus,
                            "log": (
                                f"Retrieved grounding via {tool} "
                                f"({vocab['corpus_label']})."
                            ),
                            "output": data,
                        },
                    ),
                ]
            )

        time.sleep(self._step_delay)
        summary = _format_fallback_summary(
            request.prompt,
            [{"source": item.name, "text": (canned[item.name].data if item.name in canned else item.focus)} for item in plan_items],
        )
        self._spans.write_spans(
            [
                self._span(
                    request,
                    f"{root_id}-summary",
                    root_id,
                    vocab["synthesize_title"],
                    "internal",
                    "research-lead",
                    {
                        "phase": "synthesize",
                        "log": vocab["synthesize_log"],
                        "decision": vocab["synthesize_decision"],
                        "output": summary,
                        "data_produced": summary,
                        "summary": summary,
                    },
                )
            ]
        )

        done = _now()
        # Finalize: close the running root span and flip lifecycle status truthfully.
        self._spans.finalize_span(root_id, done, "ok")
        if request.turn_id:
            self._history.update_turn_status(request.workflow_id, request.turn_id, "completed")
        self._history.update_workflow_status(request.workflow_id, "completed", done)
        self._history.update_run_status(request.run_id, "completed", done)


class _PlanItem(BaseModel):
    name: str
    focus: str
    rationale: str = ""


class _Synthesis(BaseModel):
    summary: str
    highlights: list[dict[str, str]] = []
    recommendation: str = ""
    customer_actions: list[dict[str, str]] = []
    degraded: bool = False
    degraded_reason: str = ""


_FALLBACK_PLAN_SALES: list[_PlanItem] = [
    _PlanItem(
        name="competitor-mentions",
        focus="Find peers and competitors named or alluded to across the earnings call and how they came up.",
        rationale="Surfaces the competitive pressure shaping the account narrative.",
    ),
    _PlanItem(
        name="call-scoring",
        focus="Score the earnings narrative for account urgency (demand, risk, timing).",
        rationale="Turns the transcript into a comparable, actionable account signal.",
    ),
    _PlanItem(
        name="similar-conversations",
        focus="Retrieve prior earnings calls with the closest trajectory to ground the recommendation.",
        rationale="Anchors advice in what comparable companies said in similar quarters.",
    ),
]

_FALLBACK_PLAN_FINANCE: list[_PlanItem] = [
    _PlanItem(
        name="pricing-discounting",
        focus="Extract pricing power, discount pressure, and list-vs-net dynamics from the earnings calls.",
        rationale="Surfaces the commercial terms that drive deal economics.",
    ),
    _PlanItem(
        name="contract-payment-terms",
        focus="Find guidance, capital allocation, and revenue-recognition risks discussed.",
        rationale="Flags terms that affect cash and recognition.",
    ),
    _PlanItem(
        name="margin-risk",
        focus="Assess margin erosion risk and the single best commercial counter per customer.",
        rationale="Turns transcript economics into a finance action.",
    ),
]

def _fallback_plan(settings: Settings, department: str) -> list[_PlanItem]:
    if department.strip().lower() == "finance":
        return list(_FALLBACK_PLAN_FINANCE)
    return list(_FALLBACK_PLAN_SALES)


from vastclaw_ui.k8s import sandbox_pod_name, slug_for as _slug  # noqa: E402


def _with_coverage_note(
    summary: str, missing: list[str], degraded: list[str]
) -> str:
    """Prepend an honest coverage banner to the report."""
    if not missing and not degraded:
        return summary
    lines = ["> **Incomplete coverage — read with caution**", ">"]
    if missing:
        lines += [
            f"> These planned workstreams produced no findings: {', '.join(missing)}. "
            "Their subject areas are NOT covered below.",
            ">",
        ]
    if degraded:
        lines += [f"> These workstreams ran degraded: {', '.join(degraded)}.", ">"]
    return "\n".join(lines) + "\n\n" + summary


def _format_fallback_summary(prompt: str, contributions: list[dict[str, str]]) -> str:
    """Structured markdown when NIM synthesis is unavailable."""
    lines = [
        "# Workflow analysis",
        "",
        "## Executive summary",
        f"Analysis goal: {prompt.strip()}",
        "",
    ]
    if contributions:
        lines += ["## Evidence by workstream", ""]
        for c in contributions:
            source = str(c.get("source") or "sub-agent").strip() or "sub-agent"
            text = str(c.get("text") or "").strip()
            lines.append(f"### {source}")
            if text:
                for chunk in [p.strip() for p in text.split("\n\n") if p.strip()][:4]:
                    one_line = " ".join(chunk.split())
                    lines.append(f"- {one_line[:400]}")
            else:
                lines.append("- No findings recorded.")
            lines.append("")
    return "\n".join(lines).strip()


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Drop a reasoning model's <think> preamble."""
    return _THINK_BLOCK.sub("", text or "")


def _budget(settings: Settings, floor: int) -> int:
    """Token budget for a structured call."""
    return max(floor, int(settings.model.max_tokens or 0))


def _json_obj(text: str) -> dict:
    text = _strip_think(text)
    try:
        start = text.find("{")
        end = text.rfind("}")
        obj = json.loads(text[start : end + 1], strict=False) if start >= 0 else {}
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


class KubernetesRuntime(_QaCapable):

    def __init__(
        self,
        settings: Settings,
        spans: SpanSource,
        history: HistoryStore,
        provisioner: KubernetesProvisioner,
    ) -> None:
        self._settings = settings
        self._spans = spans
        self._history = history
        self._provisioner = provisioner
        self._nim = NimClient(settings.model)

    def start_run(self, request: RunRequest) -> None:
        threading.Thread(target=self._safe_execute, args=(request,), daemon=True).start()

    def _safe_execute(self, request: RunRequest) -> None:
        try:
            self._execute(request)
        except Exception:
            _log.exception("kubernetes research run %s failed", request.run_id)
            self._fail(request)

    def _fail(self, request: RunRequest) -> None:
        now = _now()
        try:
            self._spans.finalize_span(f"{request.run_id}-root", now, "error")
            if request.turn_id:
                self._history.update_turn_status(request.workflow_id, request.turn_id, "error")
            self._history.update_workflow_status(request.workflow_id, "error", now)
            self._history.update_run_status(request.run_id, "error", now)
        except Exception:
            _log.exception("failed to record error status for run %s", request.run_id)

    def _corpus_hint(self, department: str) -> str:
        """Compact business-context summary for planning. Best-effort — '' on failure."""
        try:
            if not self._settings.mcp.enabled:
                return ""
            from vastclaw_ui.rag_client import RagMcpClient

            client = RagMcpClient(self._settings.mcp, department)
            md = client.list_call_metadata(limit=50)
            customers = [str(f.get("value") or "") for f in (md.get("customers") or [])][:12]
            stages = [str(f.get("value") or "") for f in (md.get("sales_stages") or [])][:10]
            parts = []
            if customers:
                parts.append("Customers in the corpus: " + ", ".join(c for c in customers if c))
            if stages:
                parts.append("Deal stages present: " + ", ".join(s for s in stages if s))
            return "\n".join(parts)
        except Exception as exc:  # never block planning on a metadata hiccup
            _log.warning("corpus hint failed: %s", exc)
            return ""

    def _persona(self, department: str) -> str:
        """The department's configurable role/system-prompt lens (config-driven)."""
        return self._settings.persona(department)

    def _plan(
        self,
        prompt: str,
        context: str = "",
        persona: str = "",
        department: str = "",
    ) -> list[_PlanItem]:
        del persona  # persona is embedded via planning_lead_prompt(settings, department)
        cap = max(1, self._settings.runtime.max_subagents)
        lead_instructions = planning_lead_prompt(self._settings, department)
        goal = planning_user_goal(self._settings, department, prompt, context)

        if self._nim.enabled:
            try:
                completion = self._nim.complete(
                    lead_instructions, goal, max_tokens=_budget(self._settings, 500)
                )
                raw = _strip_think(completion.text)
                start = raw.find("[")
                end = raw.rfind("]")
                items = json.loads(raw[start : end + 1], strict=False) if start >= 0 else []
                plan = [
                    _PlanItem(
                        name=_slug(str(i["name"])),
                        focus=str(i["focus"]),
                        rationale=str(i.get("rationale", "")),
                    )
                    for i in items
                    if isinstance(i, dict) and i.get("name") and i.get("focus")
                ]
                if plan:
                    return plan[:cap]
            except (ModelError, ValueError, KeyError, TypeError) as exc:
                _log.warning("plan via NIM failed, using fallback: %s", exc)
        return _fallback_plan(self._settings, department)[:cap]

    def _synthesize(
        self,
        prompt: str,
        contributions: list[dict[str, str]],
        persona: str = "",
        department: str = "",
        missing_workstreams: list[str] | None = None,
    ) -> _Synthesis:
        del persona
        joined = "\n\n".join(
            f"[{c['source']}]\n{c['text']}" for c in contributions if c.get("text")
        )
        if self._nim.enabled and joined:
            try:
                user_prompt = synthesis_user_prompt(
                    self._settings, department, prompt, joined
                )
                if missing_workstreams:
                    user_prompt += (
                        "\n\nCoverage warning: these planned workstreams returned no "
                        f"findings: {', '.join(missing_workstreams)}. Do not speculate "
                        "about their subject areas and do not imply they were "
                        "investigated; state plainly that they are not covered."
                    )
                completion = self._nim.complete(
                    synthesis_system_prompt(self._settings, department),
                    user_prompt,
                    max_tokens=_budget(self._settings, 1600),
                )
                obj = _json_obj(completion.text)
                summary = str(obj.get("summary") or "")
                highlights = [
                    {"insight": str(h.get("insight", "")), "source": str(h.get("source", ""))}
                    for h in obj.get("highlights", [])
                    if isinstance(h, dict) and h.get("insight")
                ][:8]
                customer_actions = [
                    {
                        "customer": str(a.get("customer", "")),
                        "stage": str(a.get("stage", "")),
                        "action": str(a.get("action", "")),
                        "source": str(a.get("source", "")),
                    }
                    for a in obj.get("customer_actions", [])
                    if isinstance(a, dict) and a.get("customer") and a.get("action")
                ][:24]
                if summary:
                    return _Synthesis(
                        summary=summary,
                        highlights=highlights,
                        recommendation=str(obj.get("recommendation") or ""),
                        customer_actions=customer_actions,
                    )
                reason = f"model returned no parsable summary ({str(completion.text)[:160]!r})"
                _log.warning("synthesis reply unusable, using fallback: %s", reason)
                return _Synthesis(
                    summary=_format_fallback_summary(prompt, contributions),
                    degraded=True,
                    degraded_reason=reason,
                )
            except ModelError as exc:
                _log.warning("synthesis via NIM failed, using fallback: %s", exc)
                return _Synthesis(
                    summary=_format_fallback_summary(prompt, contributions),
                    degraded=True,
                    degraded_reason=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 — see below
                _log.exception("synthesis raised unexpectedly, using fallback")
                return _Synthesis(
                    summary=_format_fallback_summary(prompt, contributions),
                    degraded=True,
                    degraded_reason=f"synthesis error: {type(exc).__name__}: {exc}",
                )
        # Model genuinely disabled or nothing to summarize — not a degradation.
        return _Synthesis(summary=_format_fallback_summary(prompt, contributions))

    def _gather_citations(self, request: RunRequest) -> list[dict[str, object]]:
        """Transcript snippets retrieved by sub-agents, attributed to their workstream."""
        out: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for span in self._spans.fetch(request.department):
            if span.run_id != request.run_id or span.kind != "agent":
                continue
            if span.agent_name == "research-lead":
                continue
            raw = span.attributes.get("evidence_json", "")
            if not raw:
                continue
            try:
                items = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                eng = str(item.get("engagement_id", ""))
                key = (eng, text[:96])
                if key in seen:
                    continue
                seen.add(key)
                out.append({**item, "source": span.agent_name})
        return out[:36]

    def _model_label(self) -> str:
        return self._settings.model.model if self._nim.enabled else "offline"

    def _mkspan(
        self,
        request: RunRequest,
        span_id: str,
        parent: str,
        name: str,
        kind: str,
        agent: str,
        attributes: dict[str, str],
        status: str = "ok",
    ) -> Span:
        start = _now()
        attrs = {**department_span_attrs(self._settings, request.department), **attributes}
        return Span(
            trace_id=request.run_id,
            span_id=span_id,
            parent_span_id=parent,
            workflow_id=request.workflow_id,
            run_id=request.run_id,
            name=name,
            kind=kind,
            agent_name=agent,
            department=request.department,
            user=request.user,
            start_time=start,
            end_time="" if status == "running" else start,
            status=status,
            attributes=attrs,
        )

    def _subagent_env_map(
        self,
        request: RunRequest,
        subagent: str,
        focus: str,
        parent: str,
        pvc_name: str,
        *,
        clone_source_pvc: str = "",
    ) -> dict[str, str]:
        """Env vars injected into every OpenShell-governed sub-agent sandbox."""
        namespace = f"vastclaw-{request.department}"
        slug = _slug(subagent)
        sandbox_name = sandbox_pod_name(request.workflow_id, slug)
        env = {
            "RUN_ID": request.run_id,
            "WORKFLOW_ID": request.workflow_id,
            "DEPARTMENT": request.department,
            "USER_NAME": request.user,
            "SUBAGENT": subagent,
            "SUBAGENT_SLUG": slug,
            "FOCUS": focus,
            "PROMPT": request.prompt,
            "DEPARTMENT_PERSONA": self._persona(request.department),
            "PARENT_SPAN_ID": parent,
            "JOB_NAME": sandbox_name,
            "PVC_NAME": pvc_name,
            "STORAGE_CLASS": self._settings.sandbox.storage_class or namespace,
            "ACCESS_MODE": self._settings.sandbox.access_mode,
            # OpenShell path writes runner config onto the PVC (see openshell_spawn).
            "VASTCLAW_CONFIG": "/workspace/.vastclaw/config.yaml",
        }
        if clone_source_pvc:
            env["CLONE_SOURCE_PVC"] = clone_source_pvc
        return env

    def _runner_config_bytes(self) -> bytes:
        """Runtime config written onto the PVC before OpenShell ExecSandbox."""
        cfg = {
            "vast": {
                "access_key": self._settings.vast.access_key,
                "secret_key": self._settings.vast.secret_key,
            },
            "vdb": self._settings.vdb.model_dump(),
            "model": self._settings.model.model_dump(),
            "mcp": self._settings.mcp.model_dump(),
            "departments": {
                name: dept.model_dump() for name, dept in self._settings.departments.items()
            },
            "runtime": self._settings.runtime.model_dump(),
            "openclaw": self._settings.openclaw.model_dump(),
            "agent_sandbox": self._settings.agent_sandbox.model_dump(),
        }
        return yaml.safe_dump(cfg).encode("utf-8")

    def _load_openshell_policy(self, department: str):
        """Read sandbox-policy.yaml from the department NemoClaw ConfigMap."""
        from kubernetes import client

        from vastclaw_ui.openshell_spawn import load_sandbox_policy

        asb = self._settings.agent_sandbox
        namespace = f"vastclaw-{department}"
        cm = client.CoreV1Api().read_namespaced_config_map(
            name=asb.blueprint_configmap, namespace=namespace
        )
        raw = (cm.data or {}).get("sandbox-policy.yaml")
        if not raw:
            raise RuntimeError(
                f"configmap/{asb.blueprint_configmap} in {namespace} missing "
                "sandbox-policy.yaml — run deploy/nemoclaw/vast-claw-nemoclaw.sh"
            )
        return load_sandbox_policy(text=raw)

    def _prepare_pvc_ownership(self, namespace: str, pvc_name: str) -> None:
        """Root one-shot pod: chown the VAST block PVC so uid 10002 can write."""
        from kubernetes import client

        mount = self._settings.runtime.workspace_mount
        fs_group = self._settings.sandbox.fs_group
        runner_uid = fs_group or 10002
        pod_name = f"vc-chown-{pvc_name}"[:63].rstrip("-")
        core = client.CoreV1Api()
        try:
            core.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                grace_period_seconds=0,
                propagation_policy="Background",
            )
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise
        body = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={"vastclaw.vastdata.com/role": "pvc-chown"},
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[
                    client.V1Container(
                        name="chown",
                        image=self._settings.runtime.agent_image,
                        image_pull_policy="IfNotPresent",
                        command=[
                            "sh",
                            "-c",
                            f"chown -R {runner_uid}:{runner_uid} {mount} 2>/dev/null || true; "
                            f"chmod -R u+rwX,g+rwX {mount} 2>/dev/null || true",
                        ],
                        volume_mounts=[
                            client.V1VolumeMount(name="workspace", mount_path=mount)
                        ],
                        security_context=client.V1SecurityContext(
                            run_as_user=0, run_as_group=0
                        ),
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="workspace",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=pvc_name
                        ),
                    )
                ],
            ),
        )
        core.create_namespaced_pod(namespace=namespace, body=body)
        deadline = time.time() + 120
        last_phase = ""
        while time.time() < deadline:
            pod = core.read_namespaced_pod(name=pod_name, namespace=namespace)
            last_phase = (pod.status.phase if pod.status else "") or ""
            if last_phase in ("Succeeded", "Failed"):
                break
            time.sleep(1)
        try:
            core.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                grace_period_seconds=0,
                propagation_policy="Background",
            )
        except client.exceptions.ApiException:
            pass
        # RWO: wait for detach before OpenShell mounts the same PVC.
        time.sleep(3)
        if last_phase != "Succeeded":
            _log.warning(
                "pvc chown pod %s/%s ended phase=%s (continuing; writes may fail)",
                namespace,
                pod_name,
                last_phase,
            )

    def _create_sandbox(
        self,
        request: RunRequest,
        subagent: str,
        focus: str,
        parent: str,
        pvc_name: str,
        *,
        clone_source_pvc: str = "",
    ) -> str:
        """CreateSandbox via the department OpenShell gateway + start agent_runner."""
        from vastclaw_ui.openshell_spawn import (
            RUNNER_CONFIG_IN_SANDBOX,
            OpenShellGateway,
            build_pvc_driver_config,
            gateway_endpoint,
        )

        namespace = f"vastclaw-{request.department}"
        slug = _slug(subagent)
        sandbox_name = sandbox_pod_name(request.workflow_id, slug)
        asb = self._settings.agent_sandbox
        mount = self._settings.runtime.workspace_mount

        # Reuse a live sandbox on continue; otherwise replace.
        if self._sandbox_pod_running(namespace, sandbox_name):
            _log.info(
                "openshell sandbox %s already Running — reusing for workflow %s",
                sandbox_name,
                request.workflow_id,
            )
            return sandbox_name

        self._delete_sandbox_openshell(request.department, sandbox_name)
        self._prepare_pvc_ownership(namespace, pvc_name)

        env = self._subagent_env_map(
            request,
            subagent,
            focus,
            parent,
            pvc_name,
            clone_source_pvc=clone_source_pvc,
        )
        policy = self._load_openshell_policy(request.department)
        endpoint = gateway_endpoint(request.department, asb.gateway_endpoint_template)
        gw = OpenShellGateway(
            endpoint,
            workspace=asb.gateway_workspace,
            timeout=asb.gateway_timeout_seconds,
        )
        try:
            ref = gw.create_sandbox(
                name=sandbox_name,
                image=self._settings.runtime.agent_image,
                environment=env,
                policy=policy,
                driver_config=build_pvc_driver_config(pvc_name, workspace_mount=mount),
                labels={
                    "vastclaw.vastdata.com/department": request.department,
                    "vastclaw.vastdata.com/workflow": request.workflow_id,
                    "vastclaw.vastdata.com/subagent": slug,
                },
                ready_timeout_seconds=asb.gateway_ready_timeout_seconds,
            )
            gw.write_file(ref.id, RUNNER_CONFIG_IN_SANDBOX, self._runner_config_bytes())
            # Long-lived: agent turn + keep-alive. timeout 0 = no command deadline.
            exec_timeout = int(
                self._settings.runtime.run_timeout_seconds
                + max(0, asb.keep_alive_seconds)
                + 120
            )
            gw.start_command_background(
                ref.id,
                ["python", "-m", "vastclaw_ui.agent_runner"],
                env=env,
                timeout_seconds=exec_timeout,
                label=sandbox_name,
                delete_when_done=sandbox_name,
            )
        finally:
            # Keep the channel open for the background exec thread; do not close here.
            pass
        return sandbox_name

    def _delete_sandbox_openshell(self, department: str, name: str) -> None:
        """Best-effort gateway delete + CR/pod cleanup so the name can be reused."""
        from vastclaw_ui.openshell_spawn import OpenShellGateway, gateway_endpoint

        asb = self._settings.agent_sandbox
        endpoint = gateway_endpoint(department, asb.gateway_endpoint_template)
        try:
            gw = OpenShellGateway(
                endpoint,
                workspace=asb.gateway_workspace,
                timeout=asb.gateway_timeout_seconds,
            )
            gw.delete_sandbox(name)
            gw.close()
        except Exception as exc:  # noqa: BLE001
            _log.info("openshell delete %s via gateway skipped: %s", name, exc)
        self._delete_sandbox_cr(f"vastclaw-{department}", name)

    def _sandbox_pod_running(self, namespace: str, name: str) -> bool:
        from kubernetes import client

        try:
            pod = client.CoreV1Api().read_namespaced_pod(name=name, namespace=namespace)
        except client.exceptions.ApiException as exc:
            if exc.status == 404:
                return False
            raise
        phase = (pod.status.phase if pod.status else "") or ""
        return phase == "Running"

    def _delete_sandbox_cr(self, namespace: str, name: str) -> None:
        from kubernetes import client

        asb = self._settings.agent_sandbox
        custom = client.CustomObjectsApi()
        try:
            custom.delete_namespaced_custom_object(
                group=asb.api_group,
                version=asb.api_version,
                namespace=namespace,
                plural=asb.plural,
                name=name,
            )
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise
        # Controller may leave the pod briefly; drop it so the RWO PVC can remount.
        try:
            client.CoreV1Api().delete_namespaced_pod(
                name=name,
                namespace=namespace,
                grace_period_seconds=0,
                propagation_policy="Background",
            )
        except client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise

    def _reap_dead_workflow_sandboxes(self, department: str, workflow_id: str) -> list[str]:
        """Delete Agent-Sandbox CRs for this workflow whose pods are not Running."""
        from kubernetes import client

        asb = self._settings.agent_sandbox
        namespace = f"vastclaw-{department}"
        custom = client.CustomObjectsApi()
        try:
            listed = custom.list_namespaced_custom_object(
                group=asb.api_group,
                version=asb.api_version,
                namespace=namespace,
                plural=asb.plural,
            )
        except client.exceptions.ApiException as exc:
            if exc.status in (404, 403):
                return []
            raise
        prefix = f"{workflow_id}-"
        removed: list[str] = []
        for item in listed.get("items", []):
            name = item.get("metadata", {}).get("name") or ""
            if not name.startswith(prefix):
                continue
            if self._sandbox_pod_running(namespace, name):
                continue
            self._delete_sandbox_openshell(department, name)
            removed.append(name)
        if removed:
            _log.info(
                "reaped %d dead sandbox(es) for workflow %s: %s",
                len(removed),
                workflow_id,
                ", ".join(removed),
            )
        return removed

    def _spawn_one_subagent(
        self,
        request: RunRequest,
        item: _PlanItem,
        root_id: str,
        clone_source: str,
    ) -> tuple[str, str, str]:
        """Provision one sub-agent PVC clone + Agent-Sandbox CR."""
        slug = _slug(item.name)
        sandbox = self._provisioner.ensure_subagent_sandbox(
            request.department,
            request.workflow_id,
            slug,
            clone_from=clone_source or None,
        )
        self._create_sandbox(
            request,
            item.name,
            item.focus,
            root_id,
            sandbox.pvc_name,
            clone_source_pvc=clone_source,
        )
        # agent_runner writes its agent span as f"{run_id}-{SUBAGENT}" (SUBAGENT=name).
        return f"{request.run_id}-{item.name}", sandbox.pvc_name, item.name

    def _checkpoint_subagent_complete(
        self, request: RunRequest, pvc_name: str, agent_name: str
    ) -> None:
        """Best-effort VolumeSnapshot of a sub-agent PVC after it finishes writing."""
        if not self._settings.sandbox.checkpoint_on_subagent_complete:
            return
        slug = _slug(agent_name)
        try:
            snap = self._provisioner.create_volume_snapshot(
                request.department,
                request.workflow_id,
                pvc_name,
                run_id=request.run_id,
                label="subagent-complete",
                name_hint=slug,
            )
        except Exception as exc:
            _log.warning(
                "sub-agent complete checkpoint failed for %s (%s): %s",
                agent_name,
                pvc_name,
                exc,
            )
            return
        parent = f"{request.run_id}-{agent_name}"
        try:
            self._spans.write_spans(
                [
                    self._mkspan(
                        request,
                        f"{request.run_id}-{slug}-checkpoint",
                        parent,
                        span_vocab(self._settings, request.department)[
                            "checkpoint_workspace_title"
                        ],
                        "internal",
                        agent_name,
                        {
                            "phase": "storage",
                            "log": (
                                f"Checkpointed sub-agent PVC {pvc_name} as "
                                f"VolumeSnapshot {snap} after completion."
                            ),
                            "decision": (
                                f"Sub-agent {agent_name} workspace snapshotted after finish."
                            ),
                            "checkpoint_snapshot": snap,
                            "sandbox_pvc": pvc_name,
                            "label": "subagent-complete",
                        },
                    )
                ]
            )
        except Exception as exc:
            _log.warning("could not record sub-agent checkpoint span: %s", exc)

    def _wait_for_subagents(
        self,
        request: RunRequest,
        agents: list[tuple[str, str, str]],
    ) -> list[str]:
        deadline = time.time() + self._settings.runtime.run_timeout_seconds
        namespace = f"vastclaw-{request.department}"
        pending = {span_id: (pvc, name) for span_id, pvc, name in agents}
        abandoned: list[str] = []
        strikes: dict[str, int] = {}
        while pending and time.time() < deadline:
            try:
                done = {
                    s.span_id
                    for s in self._spans.fetch(request.department)
                    if s.run_id == request.run_id and s.kind == "agent" and s.end_time
                }
                finished = [sid for sid in list(pending) if sid in done]
                for sid in finished:
                    pvc, name = pending.pop(sid)
                    strikes.pop(sid, None)
                    self._checkpoint_subagent_complete(request, pvc, name)
            except Exception as exc:  # never hard-fail the run on a transient fetch error
                _log.warning("polling sub-agent spans failed (%s); retrying", exc)
            for sid, (_pvc, name) in list(pending.items()):
                if not self._subagent_pod_terminal(namespace, request.workflow_id, name):
                    strikes.pop(sid, None)
                    continue
                strikes[sid] = strikes.get(sid, 0) + 1
                if strikes[sid] < 2:
                    continue
                pending.pop(sid)
                abandoned.append(name)
                _log.warning(
                    "sub-agent %s (workflow %s) pod terminated without reporting "
                    "findings; not waiting out the run timeout",
                    name,
                    request.workflow_id,
                )
            if pending:
                time.sleep(self._settings.runtime.poll_interval_seconds)
        if pending:
            _log.warning(
                "workflow %s run %s timed out waiting for sub-agents: %s",
                request.workflow_id,
                request.run_id,
                ", ".join(sorted(name for _, name in pending.values())),
            )
        return sorted(abandoned + [name for _, name in pending.values()])

    def _subagent_pod_terminal(
        self, namespace: str, workflow_id: str, agent_name: str
    ) -> bool:
        from kubernetes import client

        pod_name = sandbox_pod_name(workflow_id, _slug(agent_name))
        try:
            pod = client.CoreV1Api().read_namespaced_pod(name=pod_name, namespace=namespace)
        except client.exceptions.ApiException:
            return False
        return ((pod.status.phase if pod.status else "") or "") in ("Failed", "Succeeded")

    def _load_kube(self) -> None:
        from kubernetes import config

        if self._settings.sandbox.in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config()

    def _execute(self, request: RunRequest) -> None:
        self._load_kube()
        namespace = f"vastclaw-{request.department}"
        now = _now()
        if request.turn_id:
            self._history.update_turn_status(request.workflow_id, request.turn_id, "running")
        self._history.update_workflow_status(request.workflow_id, "running", now)

        root_id = f"{request.run_id}-root"
        vocab = span_vocab(self._settings, request.department)
        self._spans.write_spans(
            [
                self._mkspan(
                    request,
                    root_id,
                    "",
                    vocab["lead_title"],
                    "agent",
                    "research-lead",
                    {
                        "phase": "initialize",
                        "input": request.prompt,
                        "log": vocab["lead_log"],
                        "sandbox_pvc": request.sandbox.pvc_name,
                        "sandbox_view": request.sandbox.view_path,
                        "model": self._settings.model.model if self._nim.enabled else "offline",
                    },
                    status="running",
                )
            ]
        )

        plan = self._plan(
            request.prompt,
            self._corpus_hint(request.department),
            self._persona(request.department),
            request.department,
        )
        plan_json = json.dumps(
            [{"name": p.name, "focus": p.focus, "rationale": p.rationale} for p in plan]
        )
        self._spans.write_spans(
            [
                self._mkspan(
                    request,
                    f"{root_id}-plan",
                    root_id,
                    vocab["plan_title"],
                    "internal",
                    "research-lead",
                    {
                        "phase": "initialize",
                        "input": request.prompt,
                        "decision": (
                            f"Decomposed into {len(plan)} {vocab['plan_decision_suffix']}."
                        ),
                        "output": "\n".join(f"{i + 1}. {p.focus}" for i, p in enumerate(plan)),
                        "plan_json": plan_json,
                    },
                )
            ]
        )

        if self._settings.runtime.require_plan_approval and not request.auto_approve_plan:
            paused = _now()
            self._spans.write_spans(
                [
                    self._mkspan(
                        request,
                        f"{root_id}-await",
                        root_id,
                        vocab["await_title"],
                        "internal",
                        "research-lead",
                        {
                            "phase": "initialize",
                            "log": "Waiting for a human to review and approve the plan "
                            "before spawning sub-agents.",
                            "decision": "Plan proposed — awaiting approval.",
                        },
                        status="running",
                    )
                ]
            )
            if request.turn_id:
                self._history.update_turn_status(
                    request.workflow_id, request.turn_id, "awaiting_approval"
                )
            self._history.update_workflow_status(
                request.workflow_id, "awaiting_approval", paused
            )
            self._history.update_run_status(request.run_id, "awaiting_approval", "")
            return

        self._run_fanout(request, plan, root_id, namespace)

    def resume_run(self, request: RunRequest, plan: list[dict[str, str]]) -> None:
        threading.Thread(
            target=self._safe_resume, args=(request, plan), daemon=True
        ).start()

    def _safe_resume(self, request: RunRequest, plan: list[dict[str, str]]) -> None:
        try:
            self._resume(request, plan)
        except Exception:
            _log.exception("kubernetes research run %s failed on resume", request.run_id)
            self._fail(request)

    def _resume(self, request: RunRequest, plan: list[dict[str, str]]) -> None:
        self._load_kube()
        namespace = f"vastclaw-{request.department}"
        now = _now()
        root_id = f"{request.run_id}-root"
        items = [
            _PlanItem(
                name=_slug(str(p.get("name", ""))),
                focus=str(p.get("focus", "")),
                rationale=str(p.get("rationale", "")),
            )
            for p in plan
            if p.get("name") and p.get("focus")
        ]
        if not items:
            raise ValueError("cannot resume with an empty plan")
        if request.turn_id:
            self._history.update_turn_status(request.workflow_id, request.turn_id, "running")
        self._history.update_workflow_status(request.workflow_id, "running", now)
        self._history.update_run_status(request.run_id, "running", "")
        # Close the "awaiting approval" marker span and record the approved plan.
        vocab = span_vocab(self._settings, request.department)
        self._spans.finalize_span(f"{root_id}-await", now, "ok")
        self._spans.write_spans(
            [
                self._mkspan(
                    request,
                    f"{root_id}-approved",
                    root_id,
                    vocab["approved_title"],
                    "internal",
                    "research-lead",
                    {
                        "phase": "initialize",
                        "log": (
                            f"Plan approved; spawning {vocab['department_display']} "
                            "sub-agents."
                        ),
                        "decision": f"Approved {len(items)} sub-agents.",
                        "output": "\n".join(f"{i + 1}. {p.focus}" for i, p in enumerate(items)),
                    },
                )
            ]
        )
        self._run_fanout(request, items, root_id, namespace)

    def _run_fanout(
        self, request: RunRequest, plan: list[_PlanItem], root_id: str, namespace: str
    ) -> None:
        vocab = span_vocab(self._settings, request.department)

        if (
            self._settings.sandbox.wake_on_continue
            and not self._provisioner.sandboxes_alive(request.department, request.workflow_id)
        ):
            try:
                self._reap_dead_workflow_sandboxes(
                    request.department, request.workflow_id
                )
            except Exception as exc:
                _log.warning(
                    "reap dead sandboxes failed for %s (continuing): %s",
                    request.workflow_id,
                    exc,
                )
            try:
                wake = self._provisioner.wake_for_continue(
                    request.department, request.workflow_id
                )
            except Exception as exc:
                _log.warning(
                    "wake-for-continue failed for %s (continuing fan-out): %s",
                    request.workflow_id,
                    exc,
                )
                wake = None
            if wake and wake.get("woke"):
                restored = sum(
                    1
                    for a in wake.get("actions", [])
                    if a.get("action") == "restored_from_snapshot"
                )
                remounted = sum(
                    1 for a in wake.get("actions", []) if a.get("action") == "remount"
                )
                try:
                    self._spans.write_spans(
                        [
                            self._mkspan(
                                request,
                                f"{root_id}-wake",
                                root_id,
                                vocab["wake_title"],
                                "internal",
                                "research-lead",
                                {
                                    "phase": "storage",
                                    "log": (
                                        f"Sandbox pods were gone; woke storage for continue — "
                                        f"remounted {remounted} PVC(s), restored {restored} "
                                        f"from VolumeSnapshot"
                                        + (
                                            f" (head via {wake.get('head_snapshot')})."
                                            if wake.get("head_snapshot")
                                            else "."
                                        )
                                    ),
                                    "decision": (
                                        f"Continue after dead sandboxes: head={wake.get('head_action')}; "
                                        "same workflow PVC, no new head."
                                    ),
                                    "label": "wake-on-continue",
                                    "head_action": str(wake.get("head_action", "")),
                                    "head_snapshot": str(wake.get("head_snapshot", "")),
                                    "wake_actions_json": json.dumps(wake.get("actions", [])),
                                },
                            )
                        ]
                    )
                except Exception as exc:
                    _log.warning("could not record wake span: %s", exc)

        clone_source = request.workflow_id if self._settings.sandbox.clone_subagents_from_head else ""
        checkpoint_snapshot = ""
        plan_json = json.dumps(
            [{"name": p.name, "focus": p.focus, "rationale": p.rationale} for p in plan]
        )
        try:
            self._provisioner.seed_workflow_head(
                request.department,
                request.workflow_id,
                run_id=request.run_id,
                prompt=request.prompt,
                plan_json=plan_json,
            )
            if self._settings.sandbox.checkpoint_before_fanout:
                checkpoint_snapshot = self._provisioner.create_volume_snapshot(
                    request.department,
                    request.workflow_id,
                    request.workflow_id,
                    run_id=request.run_id,
                    label="pre-fanout",
                )
        except Exception as exc:
            _log.warning(
                "workflow %s storage prep failed (continuing fan-out): %s",
                request.workflow_id,
                exc,
            )
        if checkpoint_snapshot:
            vocab = span_vocab(self._settings, request.department)
            self._spans.write_spans(
                [
                    self._mkspan(
                        request,
                        f"{root_id}-checkpoint",
                        root_id,
                        vocab["checkpoint_head_title"],
                        "internal",
                        "research-lead",
                        {
                            "phase": "storage",
                            "log": (
                                f"Checkpointed head PVC {request.workflow_id} as "
                                f"VolumeSnapshot {checkpoint_snapshot} before fan-out."
                            ),
                            "decision": "Head workspace snapshotted before spawning sub-agents.",
                            "checkpoint_snapshot": checkpoint_snapshot,
                            "clone_source_pvc": clone_source,
                            "label": "pre-fanout",
                        },
                    )
                ]
            )
        # PVC clone + Agent-Sandbox CR per sub-agent — parallel after head seed/checkpoint.
        agents: list[tuple[str, str, str]] = []
        spawn_errors: list[tuple[str, str]] = []
        workers = min(len(plan), max(1, self._settings.runtime.max_subagents))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self._spawn_one_subagent, request, item, root_id, clone_source
                ): item
                for item in plan
            }
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    agents.append(fut.result())
                except Exception as exc:
                    spawn_errors.append((item.name, str(exc)))
                    _log.exception(
                        "sub-agent %s spawn failed for workflow %s",
                        item.name,
                        request.workflow_id,
                    )
        if spawn_errors:
            _log.warning(
                "workflow %s: %d/%d sub-agent spawn(s) failed",
                request.workflow_id,
                len(spawn_errors),
                len(plan),
            )
        if not agents:
            detail = "; ".join(f"{n}: {e}" for n, e in spawn_errors) or "unknown"
            try:
                self._spans.write_spans(
                    [
                        self._mkspan(
                            request,
                            f"{root_id}-spawn-error",
                            root_id,
                            "Fan-out failed",
                            "internal",
                            "research-lead",
                            {
                                "phase": "research",
                                "log": f"No sub-agent could be started: {detail}",
                                "decision": (
                                    "Aborted before synthesis — with zero contributions "
                                    "any summary would be fabricated."
                                ),
                                "error": detail,
                            },
                            status="error",
                        )
                    ]
                )
            except Exception:
                _log.exception("could not record spawn-failure span for %s", request.run_id)
            names = ", ".join(n for n, _ in spawn_errors) or "unknown"
            raise RuntimeError(f"all sub-agent spawns failed ({names})")
        silent = self._wait_for_subagents(request, agents)
        missing = sorted({n for n, _ in spawn_errors} | set(silent))

        agent_spans = [
            s
            for s in self._spans.fetch(request.department)
            if s.run_id == request.run_id and s.kind == "agent"
        ]
        contributions = [
            {"source": s.agent_name, "text": s.attributes.get("data_produced", "")}
            for s in agent_spans
            if s.attributes.get("data_produced")
        ]
        vocab = span_vocab(self._settings, request.department)
        degraded_agents = sorted(
            {
                s.agent_name
                for s in agent_spans
                if s.agent_name != "research-lead" and s.status in ("degraded", "error")
            }
        )
        synth = self._synthesize(
            request.prompt,
            contributions,
            self._persona(request.department),
            request.department,
            missing_workstreams=missing,
        )
        summary_text = _with_coverage_note(synth.summary, missing, degraded_agents)
        citations = self._gather_citations(request)
        model_label = self._model_label()
        incomplete = bool(missing)
        self._spans.write_spans(
            [
                self._mkspan(
                    request,
                    f"{root_id}-summary",
                    root_id,
                    vocab["synthesize_title"],
                    "internal",
                    "research-lead",
                    {
                        "phase": "synthesize",
                        "log": vocab["synthesize_log"],
                        "decision": vocab["synthesize_decision"],
                        "output": summary_text,
                        "data_produced": summary_text,
                        "summary": summary_text,
                        "highlights_json": json.dumps(synth.highlights),
                        "customer_actions_json": json.dumps(synth.customer_actions),
                        "recommendation": synth.recommendation,
                        "citations_json": json.dumps(citations),
                        "model": "fallback" if synth.degraded else model_label,
                        "produced_by": "fallback" if synth.degraded else model_label,
                        "synthesis_degraded": "true" if synth.degraded else "",
                        "synthesis_error": synth.degraded_reason,
                        "coverage_planned": str(len(plan)),
                        "coverage_contributed": str(len(contributions)),
                        "coverage_missing_json": json.dumps(missing),
                        "coverage_degraded_json": json.dumps(degraded_agents),
                        "coverage_complete": "false" if incomplete else "true",
                    },
                    status="degraded" if (synth.degraded or incomplete) else "ok",
                )
            ]
        )

        done = _now()
        self._spans.finalize_span(
            root_id, done, "degraded" if (synth.degraded or incomplete) else "ok"
        )
        if request.turn_id:
            self._history.update_turn_status(request.workflow_id, request.turn_id, "completed")
        self._history.update_workflow_status(request.workflow_id, "completed", done)
        self._history.update_run_status(request.run_id, "completed", done)
