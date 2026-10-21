"""OpenShell gateway client helpers for sub-agent sandbox spawn."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from google.protobuf import json_format, struct_pb2

_log = logging.getLogger(__name__)

RUNNER_CONFIG_IN_SANDBOX = "/workspace/.vastclaw/config.yaml"


def _sanitize_env_value(value: str) -> str:
    """Collapse newlines/CRs so a value is a legal OpenShell env var."""
    return " ".join(value.replace("\r", "\n").split("\n"))


def load_sandbox_policy(path: Path | None = None, *, text: str | None = None) -> Any:
    """Load an OpenShell policy YAML into a SandboxPolicy protobuf."""
    from openshell._proto import sandbox_pb2

    if text is None:
        if path is None:
            raise ValueError("load_sandbox_policy requires path or text")
        text = path.read_text()
    raw = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError("policy must be a mapping")
    if "filesystem_policy" in raw and "filesystem" not in raw:
        raw["filesystem"] = raw.pop("filesystem_policy")
    msg = sandbox_pb2.SandboxPolicy()
    try:
        json_format.ParseDict(raw, msg, ignore_unknown_fields=False)
    except json_format.ParseError as exc:
        raise ValueError(
            f"OpenShell policy is not valid for SDK {_openshell_version()}: {exc}. "
            "An unknown key is a real error — the gateway would drop it and enforce "
            "something other than what the YAML says."
        ) from exc
    return msg


def _openshell_version() -> str:
    try:
        import openshell

        return str(getattr(openshell, "__version__", "unknown"))
    except Exception:  # noqa: BLE001 — only used to decorate an error message
        return "unknown"


def build_pvc_driver_config(
    pvc_name: str,
    *,
    workspace_mount: str = "/workspace",
) -> dict[str, Any]:
    volume_name = "vastclaw-workspace"
    return {
        "kubernetes": {
            "volumes": [
                {
                    "name": volume_name,
                    "persistent_volume_claim": {
                        "claim_name": pvc_name,
                        "read_only": False,
                    },
                }
            ],
            "containers": {
                "agent": {
                    "volume_mounts": [
                        {
                            "name": volume_name,
                            "mount_path": workspace_mount,
                            "read_only": False,
                        },
                        {
                            "name": volume_name,
                            "mount_path": "/sandbox/.openshell/workspace",
                            "read_only": False,
                        },
                    ]
                }
            },
        }
    }


def driver_config_to_struct(driver_config: Mapping[str, Any]) -> struct_pb2.Struct:
    out = struct_pb2.Struct()
    json_format.ParseDict(dict(driver_config), out)
    return out


def gateway_endpoint(department: str, template: str) -> str:
    """Resolve ``host:port`` for the department gateway from a format template."""
    return template.format(department=department, ns=f"vastclaw-{department}")


class OpenShellGateway:
    """Thin wrapper around ``openshell.SandboxClient`` for one department gateway."""

    def __init__(
        self,
        endpoint: str,
        *,
        workspace: str = "default",
        timeout: float = 60.0,
        insecure: bool = True,
    ) -> None:
        self.endpoint = endpoint
        self.workspace = workspace
        self.timeout = timeout
        self.insecure = insecure
        self._client = None

    def _connect(self):
        from openshell import SandboxClient

        if self._client is not None:
            return self._client
        # E2E: gateway chart values set disableTls + allowUnauthenticatedUsers.
        self._client = SandboxClient(self.endpoint, tls=None, timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
            self._client = None

    def create_sandbox(
        self,
        *,
        name: str,
        image: str,
        environment: Mapping[str, str],
        policy: Any,
        driver_config: Mapping[str, Any],
        labels: Mapping[str, str] | None = None,
        ready_timeout_seconds: float = 300.0,
    ):
        from openshell._proto import openshell_pb2

        client = self._connect()
        spec = openshell_pb2.SandboxSpec()
        spec.environment.update(
            {k: _sanitize_env_value(str(v)) for k, v in environment.items()}
        )
        spec.template.image = image
        spec.template.driver_config.CopyFrom(driver_config_to_struct(driver_config))
        if policy is not None:
            spec.policy.CopyFrom(policy)

        _log.info(
            "OpenShell CreateSandbox name=%s endpoint=%s image=%s",
            name,
            self.endpoint,
            image,
        )
        ref = client.create(
            spec=spec,
            name=name,
            labels=dict(labels or {}),
        )
        ready = client.wait_ready(name, timeout_seconds=ready_timeout_seconds)
        _log.info(
            "OpenShell sandbox Ready name=%s id=%s phase=%s",
            ready.name,
            ready.id,
            ready.phase,
        )
        return ready

    def delete_sandbox(self, name: str) -> bool:
        import grpc

        client = self._connect()
        try:
            return client.delete(name)
        except grpc.RpcError as exc:
            # Spawn pre-deletes before create, so "no such sandbox" is the normal case.
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                _log.debug("OpenShell delete %s: not found", name)
            else:
                _log.warning("OpenShell delete %s failed: %s", name, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            _log.warning("OpenShell delete %s failed: %s", name, exc)
            return False

    def write_file(self, sandbox_id: str, remote_path: str, data: bytes) -> None:
        """Write bytes to a path inside the sandbox via exec + stdin."""
        client = self._connect()
        parent = str(Path(remote_path).parent)
        script = f"mkdir -p {parent} && cat > {remote_path}"
        result = client.exec(
            sandbox_id,
            ["sh", "-c", script],
            stdin=data,
            timeout_seconds=60,
        )
        if result.exit_code != 0:
            raise RuntimeError(
                f"write {remote_path} failed (exit={result.exit_code}): {result.stderr}"
            )

    def start_command_background(
        self,
        sandbox_id: str,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 0,
        label: str = "agent",
        delete_when_done: str = "",
    ) -> None:
        """Fire-and-forget ExecSandbox in a daemon thread (agent_runner is long-lived)."""

        def _run() -> None:
            try:
                client = self._connect()
                _log.info(
                    "OpenShell exec start label=%s sandbox_id=%s cmd=%s",
                    label,
                    sandbox_id,
                    " ".join(command),
                )
                result = client.exec(
                    sandbox_id,
                    list(command),
                    env={
                        key: _sanitize_env_value(str(value))
                        for key, value in (env or {}).items()
                    },
                    timeout_seconds=timeout_seconds,
                )
                _log.info(
                    "OpenShell exec finished label=%s exit=%s",
                    label,
                    result.exit_code,
                )
                if result.exit_code != 0:
                    _log.warning(
                        "OpenShell exec stderr label=%s: %s",
                        label,
                        (result.stderr or "")[:2000],
                    )
                    if delete_when_done:
                        _log.warning(
                            "keeping sandbox %s for post-mortem (exit=%s)",
                            delete_when_done,
                            result.exit_code,
                        )
                    return
                if delete_when_done:
                    _log.info(
                        "keep-alive over for %s — deleting sandbox (PVC is retained)",
                        delete_when_done,
                    )
                    self.delete_sandbox(delete_when_done)
            except Exception:
                _log.exception("OpenShell exec failed label=%s", label)

        threading.Thread(target=_run, name=f"openshell-exec-{label}", daemon=True).start()
