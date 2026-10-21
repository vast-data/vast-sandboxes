# Secure agentinc sandboxes on VAST

**Built on the open-source [NemoClaw](https://github.com/NVIDIA/NemoClaw) and [OpenShell](https://github.com/NVIDIA/OpenShell) stack and integrated fully with VAST AI OS**.  


---


  
One prompt becomes a supervised multi-agent research run.
A research lead plans the work, a human can approve the plan, sub-agents run
in parallel inside OpenShell sandboxes on VAST block PVCs, and findings come
back as a cited report (optional PDF).

Built on **VAST AI OS** and the NVIDIA agent stack:

| VAST | Role |
|---|---|
| **VastDB** | Corpus `earnings-calls` / `earnings21.call_chunks`, per-department memory, spans, turns, and reports. Each citation shows that table. |
| **Block CSI** (`vastdata-block`) | Head PVC per workflow, zero-copy clone per sub-agent, VolumeSnapshots, sleep/wake |
| **VMS** | Department logins. `platform.s3_user` is joined to `platform.s3_group`. New DATABASE buckets join `platform.view_policy`. |
| **S3** | Corpus and app buckets on the data VIP. A sandbox reaches them only through the policy allow-list. |

| NVIDIA | Role |
|---|---|
| **OpenShell** | Deny-by-default sandbox. Every network endpoint is `enforcement: enforce`. |
| **NemoClaw** | Per-department blueprint and sandbox policy |
| **OpenClaw** | Agent loop inside the sandbox |
| **MCP** | Only path from the sandbox to VastDB |

## Policy enforcement

OpenShell applies `deploy/nemoclaw/departments/<dept>/policies/sandbox-policy.yaml` at `CreateSandbox`. Anything not listed is denied.

- **Filesystem.** System paths are read-only. Writes are `/workspace` and `/tmp`.
- **Process.** The sandbox runs as uid `10002`.
- **Network.** Each endpoint sets `enforcement: enforce`. Hosts are exact. Wildcards do not match.
- **Egress.** That department's MCP, the NIM chat and embed ports, and the VastDB, S3, and VMS hosts from config.
- **Tools.** `transcript_search`, `similar_calls`, `list_call_metadata`.

Detail: [`deploy/nemoclaw/README.md`](deploy/nemoclaw/README.md). Design and what survives sleep/wake: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## How a run works

1. **Submit** — head PVC for the workflow; workflow + turn recorded in VastDB.
2. **Plan** — research lead (NIM) proposes focused sub-agents.
3. **Approve** — optional pause; edit the plan in the UI.
4. **Fan out** — seed + snapshot the head; clone a PVC per sub-agent; OpenShell

   `CreateSandbox` mounts each clone and runs `agent_runner` (OpenClaw).
5. **Execute** — grounding via department MCP; findings written on the PVC; spans to VastDB.
6. **Checkpoint** — VolumeSnapshot per finished sub-agent PVC.
7. **Synthesize** — lead merges findings into the Summary for **this turn**.
8. **Afterwards** — keep-alive (shell), then sandboxes sleep (PVCs stay); continue wakes

   volumes and runs a new turn (new summary).

## Departments

A **department** is the isolation boundary: namespace, StorageClass, MCP server, memory
schema, persona. Users see only their department; admins see all.

| Department | Evidence |
|---|---|
| `sales` / `finance` | Call transcripts (VastDB vector search) |

## Repo layout

```
vast-sandboxes/
├── source-code/
│   ├── workflow-ui/              backend + frontend
│   ├── mcp-server/               per-department MCP tools
│   └── ingest/                   Earnings-21 → VastDB
├── deploy/                       configs, k8s, CSI, runbooks
└── docs/
```

Images: `vc-backend`, `vc-frontend`, `vc-mcp-vast-tools`. `vc-backend` is also the
sandbox image (bundles OpenClaw). Build and push those images from this repo
before `./deploy/vast-claw-deploy.sh`.

## Getting started

Build and push first. `REG` is the registry path the cluster pulls from. Use the same tags in `deploy/config/workflow-ui.config.yaml`.

```bash
docker build -f source-code/workflow-ui/backend/Dockerfile \
  -t "$REG/vc-backend:latest" source-code/workflow-ui/backend
docker push "$REG/vc-backend:latest"

docker build -f source-code/workflow-ui/frontend/Dockerfile \
  -t "$REG/vc-frontend:latest" source-code/workflow-ui/frontend
docker push "$REG/vc-frontend:latest"

docker build -f source-code/mcp-server/Dockerfile \
  -t "$REG/vc-mcp-vast-tools:latest" source-code/mcp-server
docker push "$REG/vc-mcp-vast-tools:latest"

./deploy/vast-claw-deploy.sh             # controller + platform + MCP tools
./deploy/vast-claw-sanity.sh             # e2e
# cleanup: ./deploy/vast-claw-deploy.sh cleanup
```

Full runbook + prerequisites (VAST buckets, CSI, OpenShell, TLS):
[`deploy/README.md`](deploy/README.md).
Config keys: [`deploy/config/README.md`](deploy/config/README.md).

Corpus ingest (sales/finance RAG — Earnings-21 → NGC embedqa → VastDB):

```bash
cd source-code/ingest
pip install -e .
cp config/config.example.yaml config/config.yaml   # set NIM + VastDB secrets
export NIM_API_KEY=nvapi-...
export VAST_ACCESS_KEY=... VAST_SECRET_KEY=... VASTDB_ENDPOINT=http://...
python -m temp_ingest.run_earnings21_e2e           # or --eval10 for a quick subset
```

Full runbook: [`source-code/ingest/EARNINGS21_INGEST.md`](source-code/ingest/EARNINGS21_INGEST.md).
Point MCP at the same table via
`deploy/config/mcp-{sales,finance}.config.yaml`
(`earnings-calls` / `earnings21` / `call_chunks`), then re-run `./deploy/vast-claw-tools.sh`.

## Documentation

| Doc | What |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Design, flowchart, CSI/PVC/snap, sleep/wake, data survival |
| [`deploy/README.md`](deploy/README.md) | Deploy runbook |
| [`deploy/config/README.md`](deploy/config/README.md) | Config reference |
| [`deploy/SELF_HOSTED_NIM.md`](deploy/SELF_HOSTED_NIM.md) | Self-hosted NIMs with tooling for MCP |
| [`deploy/nemoclaw/README.md`](deploy/nemoclaw/README.md) | NemoClaw policy, RBAC, sandbox security |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Feature catalog |
| [`docs/demo-user-story.md`](docs/demo-user-story.md) | Demo walkthrough + ACL proof |
