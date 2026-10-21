# NemoClaw + security

NemoClaw publishes per-department **blueprint + sandbox policy** ConfigMaps.
OpenShell applies them at `CreateSandbox`. Together with K8s RBAC and app/MCP
ACLs, that is how agent sandboxes stay isolated on VAST.

Deploy applies this for you: `./deploy/vast-claw-deploy.sh`.
Architecture: [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

---

## Layers (who enforces what)

| Layer | Where | What it gates |
| --- | --- | --- |
| **App ACL** | Backend JWT (`auth.users`) | Which department a user can see / create |
| **K8s RBAC** | `deploy/k8s/20-rbac.yaml` | What the UI backend can do *inside* each dept namespace |
| **OpenShell policy** | `departments/<dept>/policies/sandbox-policy.yaml` | What the *agent pod* may read, write, dial, or call |
| **MCP ACL** | MCP server + policy tool allow-list | Which tools / buckets / schemas that dept can use |

OpenShell is the **only** spawn path. The backend never creates Sandbox CRs; it
provisions PVCs/snapshots, reads this ConfigMap, then calls `CreateSandbox`.

---

## Layout

```
departments/
  sales/{blueprint.yaml, policies/sandbox-policy.yaml}
  finance/...
```

Committed static files. MCP host + allowed tools match that dept. The one exception
is the `nvidia_api` allow-list: its host/port are `__NIM_*__` markers rendered at
apply time from `model.base_url` / `model.embed_base_url` in
`deploy/config/workflow-ui.config.yaml` (single source of truth for NIM endpoints).

```bash
./deploy/nemoclaw/vast-claw-nemoclaw.sh          # ConfigMaps → vastclaw-<dept>
# Prefer: ./deploy/vast-claw-deploy.sh
```

ConfigMap name: `vastclaw-nemoclaw-blueprint` (`blueprint.yaml` + `sandbox-policy.yaml`).

---

## K8s RBAC (`deploy/k8s/20-rbac.yaml`)

Subject: `default` SA in `vastclaw-observability` (the UI backend).

### Per department (`vastclaw-sales` / `finance`)

| Role | Grants | Why |
| --- | --- | --- |
| `vastclaw-ui-pvc-provisioner` | PVC create/delete; VolumeSnapshots; Jobs; Secrets; ConfigMaps **get**; pods create/delete; Sandboxes **get/list/delete** (no create) | Head PVC, clones, checkpoints, seed jobs, chown pod, read NemoClaw policy, observe/reap sandboxes |
| `vastclaw-ui-admin-control` | Deployments get/patch; pods get; **pods/exec** | Dept health / rollout restart; Artifacts shell |

### Cluster-scoped

| Role | Grants | Why |
| --- | --- | --- |
| `vastclaw-ui-pv-reaper` | PV get/list/delete | Reap Retain PVs after PVC delete / factory reset |

**Not granted (on purpose):** Sandbox **create** — only OpenShell's SA does that.

---

## Sandbox policy enforcement

Deny-by-default. Every network endpoint sets `enforcement: enforce` (the string the 0.0.85 gateway accepts). Applied at create / startup; a bad policy fails the spawn. A host or tool that is not listed fails the request.

### Filesystem + process

| Control | Effect |
| --- | --- |
| `filesystem_policy` | RO system paths; RW only `/workspace`, `/tmp` |
| `landlock` | Best-effort FS lockdown |
| `run_as_user` / `run_as_group` | Non-root UID `"10002"` (must be a **quoted** string UID) |

### Network (`network_policies`)

Egress goes through the sandbox proxy. Hosts must be **exact + lowercase** —
wildcards do not match.

| Block | Allows |
| --- | --- |
| `vast_rag_mcp` | This dept's MCP only (`vastclaw-mcp-<dept>…:8080`); MCP methods + **named tools** |
| `nvidia_api` | NIM host:ports (chat + embeddings paths only) — rendered from `model.base_url` / `embed_base_url` |
| `vastdb_data_plane` | VastDB / S3 VIPs + VMS IP (data + auth endpoints) |

Also bind which **binaries** may use each policy (`node`, `openclaw`, `python`).

### MCP tools (two filters)

1. **Policy** — `tools/call` allow-list via `params.name.any` (egress proxy).
2. **MCP server** — department fixed by `VASTCLAW_DEPARTMENT`; optional S3
   `require_bucket_scope` (`vastclaw-<dept>` / `vastclaw-<dept>-*`).

Unlisted MCP methods are denied. There is no `tool:` field — use a params matcher.

```yaml
rules:
  - allow:
      method: tools/call
      params:
        name:
          any: [transcript_search, similar_calls, list_call_metadata]
```

`json_rpc_max_body_bytes` is an **endpoint** field (embeddings payloads are large).

---

## Editing policy safely

| Do | Don't |
| --- | --- |
| List every host explicitly, lowercase | Use `*.lab` wildcards |
| Set NIM endpoint once in `model.base_url` / `embed_base_url`, re-run apply | Hand-edit `nvidia_api` host/port (it's rendered — edits are overwritten) |
| Filter tools with `params.name` | Invent keys like `tool:` (silently dropped historically; parse is strict now) |
| Quote UIDs: `"10002"` | Unquoted ints or usernames |
| Re-apply ConfigMap then use **new** sandboxes | Expect running sandboxes to pick up an edit |

After edit: `./deploy/nemoclaw/vast-claw-nemoclaw.sh` (or full deploy). Old sandboxes
keep the policy from CreateSandbox time.

---

## Constraints (short)

- Gateway Service name: `os-<dept>` — must match `agent_sandbox.gateway_endpoint_template`.
- Sandbox image needs `iproute2` (`ip` for netns).
- OpenShell **drops** our labels on pods/CRs — match sandboxes by **name**, not
  `vastclaw.vastdata.com/*` selectors.
- Sandbox main process is `sleep infinity`; delete the sandbox when done or they pile up.
- Image / command come from `blueprint.yaml`; live model URLs come from workflow-ui
  config, which is also rendered into this policy's `nvidia_api` allow-list at apply.

---

## Verify

```bash
kubectl -n vastclaw-sales get cm vastclaw-nemoclaw-blueprint -o yaml | head
kubectl -n vastclaw-sales get role,rolebinding | grep vastclaw-ui
SANITY_PREFLIGHT_ONLY=1 ./deploy/vast-claw-sanity.sh
```

Demo ACL proof (app + namespace): [`../../docs/demo-user-story.md`](../../docs/demo-user-story.md).
