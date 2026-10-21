# Architecture — OpenShell agents on VAST storage

One design story. Deploy steps live in `[deploy/README.md](../deploy/README.md)`;
operational OpenShell gotchas live in `[deploy/nemoclaw/README.md](../deploy/nemoclaw/README.md)`.

## Stack


| Layer                            | Role                                                                   |
| -------------------------------- | ---------------------------------------------------------------------- |
| **Workflow UI backend**          | Plans, approval, fan-out, synthesis, PVC/clone/snapshot, wake/continue; writes lead spans; **reads** spans → graph/metrics |
| **OpenShell gateway** (per dept) | Only spawn path — `CreateSandbox` + policy enforcement + supervisor    |
| **Agent Sandbox CRD**            | `Sandbox` objects the gateway creates (`agents.x-k8s.io`)              |
| **NemoClaw**                     | Per-dept blueprint + sandbox policy ConfigMaps                         |
| **OpenClaw**                     | Agent loop inside each sandbox (`agent_runner`); writes its own spans to VastDB |
| **VAST CSI (block)**             | Head PVC, zero-copy clones, VolumeSnapshots                            |
| **VastDB**                       | **Corpus** + **telemetry** (`telemetry.spans`) + **control** (`vastclaw_control.*` — workflows/turns/runs/reports/audit) + per-dept **memory** (`memory_<dept>`) |
| **MCP** (per dept)               | Grounded tools (RAG / memory / S3 / fs)                                |

The whole system is deployed across three kinds of namespace: `vastclaw-observability`
(the workflow-ui **backend + frontend**), one `vastclaw-<dept>` per department (its
OpenShell gateway, MCP server, sandbox pods, and PVCs), and the shared VastDB behind
all of them. There is **no separate metrics stack** (no Prometheus / OpenTelemetry
collector / Grafana / Jaeger) — VastDB is the single system of record for telemetry.


There is **no direct Sandbox-CR fallback**. Sub-agents always go through OpenShell.

## Policy enforcement

Each department policy is `deploy/nemoclaw/departments/<dept>/policies/sandbox-policy.yaml`. OpenShell loads it at `CreateSandbox`. The mode on every network endpoint is the string `enforce` (deny-by-default).

| Surface | Enforced as |
| --- | --- |
| Filesystem | Read-only `/usr`, `/lib`, `/bin`, `/etc`, `/app`. Read-write `/workspace` and `/tmp` on the VAST block PVC |
| Network | Exact host allow-list: department MCP, NIM chat and embed, VastDB endpoint, S3 endpoint, VMS |
| MCP | Named tools only: `transcript_search`, `similar_calls`, `list_call_metadata` |
| Process | uid/gid `10002` |

A host or tool that is not in that file does not leave the sandbox. Operational detail: [`deploy/nemoclaw/README.md`](../deploy/nemoclaw/README.md).

```
                    ┌──────────── UI / backend ────────────┐
  prompt ──────────▶│ plan → approve → fan-out → synthesize │◀── reads spans → graph
                    └──────┬───────────────────────┬────────┘        ▲
                           │ CreateSandbox          │ lead spans      │
                           ▼                        ▼                 │
                    ┌──────── OpenShell gateway ──┐  │            ┌────┴─────┐
                    │ policy + supervisor + Exec  │  └───────────▶│  VastDB  │
                    └──────┬──────────────────────┘               │ telemetry│
                           ▼                                       │ +control │
              Sandbox CR + pod (vc-backend) + mounted PVC          │ +corpus  │
                           │                    │ sub-agent spans  │ +memory  │
              MCP ──▶ VastDB / S3               └─────────────────▶└──────────┘
              findings on /workspace
```

Note the telemetry path: sub-agents write their **own** spans **directly to VastDB**
(using the vdb creds injected into the sandbox), not back through the gateway or the
backend. The backend only *reads* spans to build the graph and metrics.



## One run

```
Submit prompt
   │  head PVC  = workflow id          (VAST block, Retain)
   │  turn + run recorded in VastDB
   ▼
Research lead plans (NIM) ──▶ awaiting_approval (optional)
   ▼
Seed head PVC ──▶ VolumeSnapshot "pre-fanout"
   ▼
For each sub-agent (parallel):
   clone PVC from head          (zero-copy, VAST block)
   chown PVC → uid 10002        (root one-shot pod)
   OpenShell CreateSandbox      (mounts PVC at /workspace + /sandbox/.openshell/workspace)
   write /workspace/.vastclaw/config.yaml   (ExecSandbox stdin)
   ExecSandbox → agent_runner   (RAG pre-fetch + OpenClaw turn)
   write <subagent>.md + artifacts on the PVC
   VolumeSnapshot when done
   ▼
Lead synthesizes cited summary ──▶ Summary tab (this run only)
   ▼
Keep-alive window (shell works) ──▶ then sandbox deleted; PVCs + snaps stay
```

Continue (another turn) = **new run_id**. Graph and Summary show the latest turn only;
History keeps every prompt. A regenerated summary replaces the previous one in the UI
(save a PDF if you need an older copy).

## Observability & telemetry (spans, graph, metrics)

Observability here is **span-first and VastDB-native**. Every meaningful step — the
research lead planning, each checkpoint/clone/snapshot, every sub-agent turn, each MCP
/ RAG tool call, synthesis, wake, and Quick Q&A — is recorded as a **span**. There is
no side telemetry system: the spans *are* the metrics, the audit trail, and the source
the DAG is drawn from.

### The stack (what runs where)

| Component | Namespace | Observability role |
| --- | --- | --- |
| Workflow UI **frontend** | `vastclaw-observability` | Renders the live DAG, timelines, per-node metrics, history |
| Workflow UI **backend** | `vastclaw-observability` | Writes **lead** spans; **reads** all spans → builds graph + summaries; serves `/api/v1/…` |
| Sub-agent `agent_runner` | `vastclaw-<dept>` (sandbox pod) | Writes its **own** agent + tool/RAG spans straight to VastDB |
| **VastDB** | shared cluster | System of record: telemetry, control, corpus, memory |

No Prometheus / OpenTelemetry collector / Grafana / Jaeger is deployed. The span record
is deliberately **OTel-shaped** (`trace_id`, `span_id`, `parent_span_id`, `kind`,
`attributes`), so it could be exported to an OTLP backend later, but today it is written
directly to VastDB.

### Where everything is stored (all in bucket `vastclaw-db`)

| Plane | Schema | Table(s) | Holds |
| --- | --- | --- | --- |
| **Telemetry** | `telemetry` | `spans` | Every span: lead + sub-agent + tool/RAG (metrics live in `attributes_json`) |
| **Control** | `vastclaw_control` | `workflows`, `workflow_turns`, `runs`, `reports`, admin audit | Workflow/turn/run system-of-record + versioned report PDFs (binary) |
| **Memory** | `memory_<dept>` | per-dept | Agent long-term memory (separate from telemetry) |

The `spans` table columns are: `trace_id, span_id, parent_span_id, workflow_id, run_id,
name, kind, agent_name, department, user, start_time, end_time, status, attributes_json`.
All quantitative metrics — latency (`end_time − start_time`), model name, token usage,
cost, tool/RAG hit counts, coverage (`coverage_planned/contributed/missing`), and flags
like `synthesis_degraded` / `woke_volumes` — are carried in **`attributes_json`**, which
is why new metrics never need a schema migration.

### How metrics flow: department → observability → VastDB

```
 vastclaw-<dept> (sandbox pod)                 vastclaw-observability
 ┌───────────────────────────┐                ┌──────────────────────────┐
 │ agent_runner (OpenClaw)    │                │ workflow-ui backend       │
 │  • agent span              │                │  • lead / plan / synth    │
 │  • RAG / MCP tool spans    │                │    checkpoint / wake / QA │
 │  ensure_schema()           │                │    spans                  │
 │  write_spans(...)          │                │  write_spans(...)         │
 └──────────────┬─────────────┘                └────────────┬──────────────┘
                │  vdb creds from                           │
                │  /workspace/.vastclaw/config.yaml         │
                ▼                                            ▼
        ┌──────────────────────────  VastDB  ──────────────────────────┐
        │  telemetry.spans   (all spans, both writers, same table)      │
        └───────────────────────────────┬──────────────────────────────┘
                                         │ fetch(department)  ← ACL predicate
                                         ▼
                            backend build_graph() / summarize()
                                         │
                                         ▼
                              UI: DAG + metrics + history
```

Two independent writers, one table. Sub-agents run in the **department** namespace and
persist spans **themselves** — the vdb access/secret keys are injected into the sandbox
via `/workspace/.vastclaw/config.yaml` (the `ExecSandbox` config-inject step), so an
agent reports telemetry even though the backend never touches its pod. The backend, in
the **observability** namespace, writes the orchestration spans and is the only reader.

### The graph is derived, not stored

There is **no stored graph object**. On every workflow-detail read the backend calls
`build_graph(workflow_id, spans, run_id=last_run_id)`:

- it `fetch`es spans for the caller's department (the `department == <dept>` predicate is
  also the **ACL** — a user only ever sees their department's spans),
- scopes them to the workflow's `last_run_id` (so multi-turn history doesn't stack
  duplicate roots),
- projects **span → node** and **`parent_span_id` → `span_id`** into edges.

So the live DAG, node status, and per-node metrics the UI shows are a **query over
`telemetry.spans`**, recomputed each read. `summarize(spans)` builds the workflow-list
rollups (agent counts, status) the same way. This is why the graph survives sleep/wake
and pod deletion for free: the pods are gone, but their spans remain and the DAG is just
re-projected.

## Storage model (CSI / PVC / snapshot)


| Object         | Name pattern                      | Who creates               | Survives sleep?               |
| -------------- | --------------------------------- | ------------------------- | ----------------------------- |
| Head PVC       | `<workflow_id>`                   | backend                   | yes (`Retain`)                |
| Sub-agent PVC  | `<workflow_id>-<slug>`            | backend (clone from head) | yes                           |
| VolumeSnapshot | `snap-…` / pre-fanout / per-agent | backend                   | yes                           |
| Sandbox / pod  | same as sub-agent PVC name        | **OpenShell gateway**     | no — deleted after keep-alive |


Backend owns volumes. OpenShell only mounts the PVC we already provisioned.
PVC and snapshot labels (`vastclaw.vastdata.com/*`) are ours and safe to select on.
Sandbox **pods** must be matched by **name** — OpenShell drops our labels on pods/CRs
(see nemoclaw README).

Everything an agent touches — head PVC, per-sub-agent clones, and snapshots — is native
**VAST block (CSI)**: the clones are **zero-copy** (`dataSource` off the head PVC) and the
checkpoints are VAST **VolumeSnapshots**. Agent state is therefore VAST-resident storage,
not local scratch, and lives in one place alongside the VastDB corpus/spans.

### Workspace mounts: `/workspace` vs `/sandbox`

The agent does its work in **`/workspace`** — that's where `agent_runner` + OpenClaw run,
where Landlock allows writes, and where `VASTCLAW_CONFIG` resolves
(`/workspace/.vastclaw/config.yaml`). The **same** VAST PVC is also bind-mounted at
`/sandbox/.openshell/workspace`:

```
VAST block PVC  (<workflow_id> or <workflow_id>-<slug>)
   ├── mounted at /workspace                     ← agent works here
   └── mounted at /sandbox/.openshell/workspace  ← disables OpenShell's default
```

This is deliberate: OpenShell would otherwise auto-provision **its own** workspace PVC at
`/sandbox`. Mounting *anything* under `/sandbox` suppresses that, so **all** agent state
lands on our VAST PVC — never on an OpenShell-managed volume we don't control or snapshot.

### Preparing the PVC (out-of-band — because OpenShell owns the pod)

OpenShell (not the backend) creates the sandbox pod, so our normal `prepare-sandbox`
initContainers do **not** run there, and the PVC is **RWO VAST block** — the backend can't
write into it directly (it's attached to the sandbox pod, not the backend). So three
out-of-band steps prepare it. This is what can look like "it's local and files are copied
in by hand" — it isn't; every step targets the **VAST PVC**:

| Step | Mechanism | Purpose |
| --- | --- | --- |
| **Seed** | busybox **Job** mounts the head PVC | writes `.vastclaw/run-context.json` *before* fan-out; zero-copy clones inherit it |
| **chown** | root one-shot **pod** mounts the PVC | `chown` to uid `10002` so the sandbox user can write (replaces the init step OpenShell skips) |
| **Config inject** | `ExecSandbox` (stdin) after Ready | pushes `/workspace/.vastclaw/config.yaml` (carries secrets → runtime, not baked into the image) |

Net: the backend provisions + seeds VAST volumes; OpenShell only mounts them and runs the
agent. Nothing assumes local disk, and nothing persists outside VAST.

## Agent lifecycle & data survival

```
  spawn ──▶ Running (agent_runner via Exec) ──▶ findings on PVC
                │
                │ keep_alive_seconds (default 900)
                │   · interactive shell works
                │   · UI: sandboxes_alive = true
                ▼
           delete sandbox (pod gone; PVC + snaps retained)
                │
                │ Continue while pods are gone
                ▼
           wake: remount retained PVCs (or restore from snapshot if needed)
                │  OpenClaw session files on the PVC resume the conversation
                ▼
           new turn fan-out + new synthesis
```

What survives where:


| Data                                  | Lives on             | After sleep                   |
| ------------------------------------- | -------------------- | ----------------------------- |
| Findings, workspace, OpenClaw session | sub-agent / head PVC | remounted on wake             |
| Checkpoints                           | VolumeSnapshots      | used if a PVC must be rebuilt |
| Spans, turns, summary attrs           | VastDB               | always                        |
| Versioned PDF                         | VastDB `reports`     | always                        |


Sleep/wake keeps PVCs (pods go away).

## UI notes that match this design

- **Graph nodes** like Plan / Wake volumes / Checkpoint / Synthesize are lead milestones (siblings of sub-agents). They are leaves on purpose.
- **Wake volumes** appears only when continue remounted after sandboxes were gone.
- Header / History show **Turn #N** and a **Woke volumes** chip when that turn woke storage.
- Live shell works only while the sandbox pod is still in keep-alive.

