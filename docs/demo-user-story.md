# Demo user story

Show **NVIDIA agentic workflows on VAST** as the persistent layer: cloneable sandboxes,
checkpoints, sleep/wake, department isolation, and a full audit trail.

Deploy first: [`../deploy/README.md`](../deploy/README.md). Deep dive: [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## What you are proving

1. **Same evidence, different roles** — sales and finance read the same public
   earnings-call corpus (Earnings-21) through different personas (config, not code).
2. **VAST holds agent state** — block PVCs + snapshots + VastDB spans survive when pods die.
3. **Department ACL** — each VMS user sees only their department; admin observes all.
4. **Policy enforcement** — the sandbox is deny-by-default (`enforcement: enforce`). It can read VastDB only through its MCP tools, and only the VastDB, S3, and VMS hosts in its policy.

| User | Auth | Department | Lens |
| --- | --- | --- | --- |
| `<SALES_USER>` | VMS | sales | demand / competition / next steps |
| `<FINANCE_USER>` | VMS | finance | guidance / margin / capital |
| `<ADMIN_USER>` | local | all (viewer) | dashboard only — do not create workflows |

Personas live under `departments.*` in `deploy/config/workflow-ui.config.yaml`.

---

## Scenario (sales)

Sign in as `<SALES_USER>` and prompt something like:

> For General Electric's earnings call, what did management say about industrial
> orders, aviation recovery, and competitive pressure? Per theme, cite the signal
> and the single highest-impact next step for the account team.

| Step | What happens | On VAST / NVIDIA |
| --- | --- | --- |
| 1 | Prior context (if any) | VastDB turns + retained PVC workspace |
| 2 | Lead plans sub-agents | NIM → plan span |
| 3 | You approve the plan | Nothing spawned yet |
| 4 | Snapshot head PVC | VolumeSnapshot |
| 5 | Fan-out | Block clone + OpenShell `CreateSandbox` per agent |
| 6 | Ground findings | VastDB vector search via MCP |
| 7 | Checkpoint agents | VolumeSnapshot per PVC |
| 8 | Synthesize | Summary (+ optional PDF in VastDB) |
| 9 | Sleep / continue | Pods gone after keep-alive; wake remounts PVCs |
| 10 | Audit | Spans in VastDB → live DAG |

Repeat the **same prompt** as `<FINANCE_USER>` — empty workflow list at first (sales is
invisible), then a deal-economics briefing instead of a sales one.

## Walk the UI

| Tab | Look for |
| --- | --- |
| Graph | Live DAG; sub-agents show `openclaw` or `openclaw-failed` |
| Decisions / Timeline | Why each step ran |
| Summary | Role-framed report |
| Artifacts | Files on the PVC; **Open shell** while keep-alive lasts |
| Storage | Head PVC, clones, snapshots, wake chip on continue |
| History | All prompts / turns |

Model badge (header): generation, OpenClaw, embeddings endpoints.

---

## Prove ACL (API)

```bash
BASE=https://vastclaw.solutions.vastdata.com

SALES=$(curl -sk $BASE/api/v1/login -H 'Content-Type: application/json' \
  -d '{"username":"<SALES_USER>","password":"<VAST_PASSWORD>"}' | jq -r .access_token)
FIN=$(curl -sk $BASE/api/v1/login -H 'Content-Type: application/json' \
  -d '{"username":"<FINANCE_USER>","password":"<VAST_PASSWORD>"}' | jq -r .access_token)
ADMIN=$(curl -sk $BASE/api/v1/login -H 'Content-Type: application/json' \
  -d '{"username":"<ADMIN_USER>","password":"<ADMIN_PASSWORD>"}' | jq -r .access_token)

curl -sk $BASE/api/v1/me -H "Authorization: Bearer $SALES" | jq   # department=sales
curl -sk $BASE/api/v1/workflows -H "Authorization: Bearer $SALES" | jq '[.[].department] | unique'
curl -sk $BASE/api/v1/workflows -H "Authorization: Bearer $FIN"   | jq '[.[].department] | unique'

# Cross-dept read denied (use a sales workflow id):
curl -sko /dev/null -w '%{http_code}\n' \
  $BASE/api/v1/workflows/$WF/detail -H "Authorization: Bearer $FIN"   # → 404

curl -sk $BASE/api/v1/workflows -H "Authorization: Bearer $ADMIN" | jq '[.[].department] | unique'
```

Auth: VMS validates password → app JWT with department. Department is never client-supplied
on create. Users must exist in VMS **and** in `auth.users`.

---

## Prove isolation (cluster)

```bash
kubectl -n vastclaw-sales   get pods,pvc,sandboxes
kubectl -n vastclaw-finance get pods,pvc,sandboxes
```

Sandboxes and PVCs only appear in their own namespace. MCP is department-pinned.

---

## Tips

- Skip plan approval: `runtime.require_plan_approval: false`, then re-run deploy.
- Shared RAG corpus is fine — **persona** is the differentiator; memory schemas are per dept.
- Orphaned login (valid VMS user not in `auth.users`) → rejected.
