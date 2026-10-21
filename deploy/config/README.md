# `workflow-ui.config.yaml`

Copy `workflow-ui.config.example.yaml` to `workflow-ui.config.yaml` and fill the copy.
The copy and `vast-access.yaml` (the key deploy creates) are gitignored.

That file is rendered into the K8s Secret mounted at `/etc/secrets/config.yaml`.

```bash
./deploy/vast-claw-deploy.sh   # applies secret + restarts backend
```

A config edit does nothing until you re-run deploy. Unknown keys are silently ignored
(Pydantic). Defaults live in `source-code/workflow-ui/backend/src/vastclaw_ui/config.py`.

---

## Quick map

| Section | Purpose |
| --- | --- |
| `vast` | App-user keys. Placeholders are filled into gitignored `vast-access.yaml` |
| `vdb` | VastDB endpoint + `vastclaw-db` buckets/schemas |
| `s3` | Data-plane S3 VIP (probes / MCP `s3_*`) — different from `vdb` |
| `model` | Generation + embeddings endpoints |
| `runtime` | `kubernetes` vs `local`, timeouts, agent image |
| `openclaw` | In-sandbox agent loop settings |
| `sandbox` | Block PVC / clone / snapshot / wake |
| `agent_sandbox` | OpenShell gateway + keep-alive |
| `auth` | JWT + VMS users → departments |
| `tls` | Ingress certificate paths (deploy only; the app ignores this) |
| `ingress` | Public UI host (deploy and sanity; the app ignores this) |
| `platform` | Buckets, S3 user (`s3_user` in `s3_group`), VMS role, corpus table. Deploy creates them. The app ignores this. |
| `departments` | Personas, evidence type, UI features |

MCP packs: `mcp-<dept>.config.yaml` (applied by deploy).

---

## `vast` / `vdb` / `s3`

| Key | Meaning |
| --- | --- |
| `vast.access_key` / `secret_key` | App user. Placeholders: deploy writes `vast-access.yaml` and does not edit this file |
| `vdb.endpoint` | Protocol VIP FQDN (not VMS) |
| `vdb.telemetry_*` / `control_*` | Spans vs workflows (usually same `vastclaw-db`) |
| `s3.endpoint` | S3 data VIP |
| `*_ssl_verify` | Lab: `false` |

---

## `model`

Two legs — may be different hosts.

| Key | Meaning |
| --- | --- |
| `enabled` | Off → offline fallback text |
| `base_url` / `api_key` | Generation (plan, OpenClaw, synthesize) |
| `allow_anonymous` | Empty key = no auth (local NIM), not disabled |
| `model` | Exact id from `/v1/models` |
| `max_tokens` | Per call (raise for reasoning models, e.g. 4096) |
| `embed_base_url` / `embed_api_key` / `embed_model` / `embed_dim` | Query embeds; `embed_dim` = VastDB column width |

**NGC hosted:** gen + embed `https://integrate.api.nvidia.com/v1`, `api_key: "nvapi-…"`,
`allow_anonymous: false`. No local GPUs; tool calling handled server-side.

**Self-hosted NIM:** gen `http://<NIM_HOST>:<CHAT_PORT>/v1`, embed `:<EMBED_PORT>/v1`, empty
keys + `allow_anonymous: true`. Use **IP**, not hostname; ports are your choice (the NIM
serves on 8000 inside the container). Full bring-up (incl. the streaming tool-calling
parser patch) is in [`../SELF_HOSTED_NIM.md`](../SELF_HOSTED_NIM.md).

Either way, `base_url` / `embed_base_url` are the **single source of truth** for the NIM
endpoint: `deploy/nemoclaw/vast-claw-nemoclaw.sh` renders them into each department's
`sandbox-policy.yaml` `nvidia_api` allow-list at apply time — don't edit the policy by hand.

Don't change `embed_model` / `embed_dim` without re-ingesting the corpus. The chat NIM
needs the streaming tool-calling parser **and** the function-calling flags; on recent NIM
builds (2.0.10) those flags must be forwarded via `NIM_PASSTHROUGH_ARGS` or `tool_choice`
requests fail with HTTP 400 — see [`../SELF_HOSTED_NIM.md`](../SELF_HOSTED_NIM.md).

```bash
curl -s http://<NIM_HOST>:<CHAT_PORT>/v1/models | jq -r '.data[].id'
kubectl -n vastclaw-sales run nimcheck --rm -it --restart=Never --image=curlimages/curl -- \
  curl -sS -m 5 http://<NIM_HOST>:<CHAT_PORT>/v1/models
```

---

## `runtime` / `openclaw`

| Key | Meaning |
| --- | --- |
| `runtime.mode` | `kubernetes` = real sandboxes; `local` = in-process demo |
| `runtime.agent_image` | `vc-backend` (API + OpenClaw; no separate agent image) |
| `runtime.require_plan_approval` | Pause before fan-out |
| `runtime.run_timeout_seconds` | Orchestrator wait for all sub-agents |
| `runtime.max_subagents` | Cap planned workstreams |
| `openclaw.timeout_seconds` | Per agent invocation |
| `openclaw.max_attempts` | Retries before `degraded` |
| `openclaw.model_primary` | Empty → `nvidia/<model.model>` |

Keep: `run_timeout_seconds` > `openclaw.timeout_seconds × max_attempts`.

No NIM fallback — OpenClaw failure → DAG `openclaw-failed`.

---

## `sandbox` / `agent_sandbox`

| Key | Meaning |
| --- | --- |
| `sandbox.storage_class` | `vastdata-block` |
| `clone_subagents_from_head` / `seed_head_before_fanout` | Zero-copy PVC fork + seed |
| `checkpoint_*` / `wake_on_continue` | VolumeSnapshot + remount on continue |
| `max_concurrent_snapshots` / `snapshot_*` | Soften VMS 503 bursts |
| `agent_sandbox.gateway_endpoint_template` | `os-{department}.{ns}.svc…:8080` |
| `keep_alive_seconds` | Sandbox stays up for "Open shell" (0 = exit now) |
| `blueprint_configmap` | `vastclaw-nemoclaw-blueprint` |

---

## `auth` / `departments`

| Key | Meaning |
| --- | --- |
| `jwt_secret` | App JWT; rotate: `openssl rand -hex 32` |
| `vms_host` / `vms_port` / `tenant_name` | VMS login (mgmt, not data VIP) |
| `users.<name>.department` | ACL scope |
| `users.<name>.local` / `password` | Skip VMS (e.g. `admin`) |
| `users.<name>.is_admin` | All depts + admin dashboard |
| `departments.*.persona` | Role lens for lead / agents / synthesis |
| `evidence_type` | Evidence rendering type (currently `transcript`) |
| `ui_features` / `mcp_tool_filter` / `example_prompts` | UI + tool scope |

---

## After a change

1. Re-run `./deploy/vast-claw-deploy.sh`.
2. UI model badge → gen / OpenClaw / embed endpoints.
3. `./deploy/vast-claw-sanity.sh` → `deploy/sanity-runs/latest/`.
