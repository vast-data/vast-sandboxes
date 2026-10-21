# Feature catalog

**Secure agents on VAST** run NVIDIA (NemoClaw / OpenShell / OpenClaw) workflows with
**VAST** as the persistent layer — storage, memory, audit, and department isolation —
so agents can run at enterprise scale without losing state when pods go away.

Mechanics: [`ARCHITECTURE.md`](ARCHITECTURE.md). Deploy: [`../deploy/README.md`](../deploy/README.md).

---

## Isolation

| Feature | What it does |
| --- | --- |
| Department boundary | `sales` / `finance` — each has its own K8s namespace, MCP server, persona, and VastDB memory schema |
| VastDB identity | One S3 user, `platform.s3_user` in `platform.s3_group`, for both departments |
| VMS login | UI users (`sales-agent-1`, `finance-agent-1`) authenticate via VMS; app JWT carries `{sub, department, is_admin}` |
| Scoped APIs | Workflows and stats are filtered by JWT department (`admin` sees all) |
| Sandbox placement | OpenShell sandboxes + PVCs only in `vastclaw-<dept>` |
| MCP identity | Tools pinned to the department; optional `s3_*` bucket scope |

---

## Persistent agent storage (VAST block)

| Feature | What it does |
| --- | --- |
| Head PVC per workflow | One RWO block volume (`vastdata-block`); survives sleep |
| Zero-copy clone per sub-agent | Each agent gets its own PVC forked from the head |
| VolumeSnapshots | Checkpoint head (pre-fanout) and each sub-agent PVC |
| Sleep / wake | After keep-alive, pods delete; PVCs stay. Continue remounts (or restores from snapshot) |
| PV reaper | On reset/PVC delete, bound Retain PVs are cleaned up |

---

## Policy enforcement (OpenShell)

Deny-by-default. The policy is `deploy/nemoclaw/departments/<dept>/policies/sandbox-policy.yaml`, applied at `CreateSandbox`. Each network endpoint is `enforcement: enforce`.

| Control | What it allows |
| --- | --- |
| Filesystem | Read-only system paths. Writes only on `/workspace` and `/tmp` (the VAST block PVC) |
| Network | Exact hosts: this department's MCP, NIM chat + embed, VastDB, S3, and VMS |
| MCP tools | `transcript_search`, `similar_calls`, `list_call_metadata` |
| Identity | Sandbox uid `10002`. VastDB queries use `platform.s3_user` in `platform.s3_group` |

Full rules: [`../deploy/nemoclaw/README.md`](../deploy/nemoclaw/README.md).

## Agent runtime (NVIDIA)

| Feature | What it does |
| --- | --- |
| OpenShell `CreateSandbox` | Only spawn path — policy + supervisor + PVC mount |
| OpenClaw in-sandbox | Real agent loop with MCP tools, workspace, shell on the PVC |
| Shared `vc-backend` image | API and agent are the same image (OpenClaw bundled) |
| Split gen / embed endpoints | Chat NIM vs embed NIM (IP-reachable from sandboxes) |
| Plan approval | Optional pause before any sandbox is created |
| No silent fallback | OpenClaw failure → `degraded` / `openclaw-failed` on the DAG |

---

## Memory, RAG & history (VastDB)

| Feature | What it does |
| --- | --- |
| Vector memory | Per-dept schemas in `vastclaw-db` (`array_cosine_distance`) |
| RAG corpus | Earnings-21 calls in VastDB (`earnings-calls` / `earnings21.call_chunks`). Each citation records that table |
| Event history | `workflows` / `turns` / `runs` — continue with prior context |
| OTel-shaped spans | Durable audit trail that drives the live DAG |
| Versioned PDF reports | Branded PDFs stored in VastDB (binary column) |

---

## Workflow UI

| Feature | What it does |
| --- | --- |
| Live DAG | research-lead → sub-agents → tools → synthesize |
| Node inspector | Input, decision, log, duration, tokens |
| Tabs | Graph, Decisions, Timeline, Summary, Quick Q&A, Artifacts, Storage, History |
| Artifacts + shell | Files from the PVC; WebSocket shell while keep-alive lasts |
| Department dashboard | Stats from VastDB when no workflow is selected |
| Admin dashboard | Cross-dept health, tokens, activity; factory reset (admin) |

---

## Ops conventions

| Feature | What it does |
| --- | --- |
| `vastclaw-` prefix | Namespaces, pods, buckets for traceability |
| Single deploy script | `./deploy/vast-claw-deploy.sh` — no hand-applied app manifests |
| VAST objects | Deploy creates the tenant check, users, DATABASE buckets, and VastDB tables from the workflow config |
| Build & push images | `vc-backend` / `vc-frontend` / `vc-mcp-vast-tools` — no CI |

Demo walkthrough: [`demo-user-story.md`](demo-user-story.md).
