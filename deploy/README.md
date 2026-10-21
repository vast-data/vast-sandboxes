# Deploy — secure agents on VAST

One script deploys the app; one script cleans it up. Deploy creates the VAST
tenant check, S3 user, UI logins, DATABASE buckets, and VastDB tables from
`deploy/config/workflow-ui.config.yaml`.

Departments: `sales`, `finance`.

---

## Scripts

| Script | What it does |
| --- | --- |
| `./deploy/vast-claw-deploy.sh` | Full bring-up (controller → platform → MCP tools) |
| `./deploy/vast-claw-deploy.sh cleanup` | Tear down the app. Keeps the corpus tables, bucket, and S3 key |
| `./deploy/vast-claw-deploy.sh cleanup-all` | Same teardown, and drops the corpus tables, bucket, and S3 key |
| `./deploy/csi/vast-block-csi.sh` | Install block CSI (`vastdata-block`) — once, before agents |
| `./deploy/csi/vast-block-csi.sh cleanup` | Remove block CSI (delete its PVCs first) |
| `./deploy/lib/check_prereqs.sh <config>` | Preflight only (also runs inside deploy) |
| `./deploy/vast-claw-sanity.sh` | End-to-end smoke after deploy |

---

## Before you deploy

1. **Operator tools:** `kubectl`, `helm`, `docker`, `python3`, registry pull access, and your TLS certificate/key.
2. **Cluster:** existing VAST CSI (`vastcsi` / `vast-mgmt`). For agents: block CSI ready (below).
3. **Config:** `cp deploy/config/workflow-ui.config.example.yaml deploy/config/workflow-ui.config.yaml` and fill the copy. Details: [`config/README.md`](config/README.md). The copy is gitignored. MCP packs are rendered from it.
4. **VAST endpoints** in that same file (`auth.vms_host`, `s3.endpoint`, `vdb.endpoint`). Deploy creates the rest:
   - tenant `auth.tenant_name` (must already exist, or the VMS login must be allowed to create it)
   - S3 user `platform.s3_user`, joined to `platform.s3_group`. Both departments use that one key. If no key exists yet, deploy stores it in `deploy/config/vast-access.yaml`
   - a VMS login for each non-local `auth.users` entry (`platform.manager_role`, password on the user)
   - DATABASE+S3 buckets for the control/telemetry bucket and `platform.corpus_bucket`
   - schemas and tables that are not already there (control, spans, per-department memory, corpus)
   - corpus rows when `platform.corpus_load: true` and `call_chunks` is missing or empty. A table that already has rows is left as-is. The load runs in a cluster pod; the deploy host does not need the data VIP.

   VMS admin login: `platform.vms_username` / `vms_password`, or leave them empty to reuse `deploy/csi/vast-block-csi.config.yaml`. The admin password is not mounted into pods.
5. **Images:**

```bash
# REG is the registry path the cluster pulls from. Use the same tags in workflow-ui.config.yaml.
docker build -f source-code/workflow-ui/backend/Dockerfile \
  -t "$REG/vc-backend:latest" source-code/workflow-ui/backend
docker push "$REG/vc-backend:latest"

docker build -f source-code/workflow-ui/frontend/Dockerfile \
  -t "$REG/vc-frontend:latest" source-code/workflow-ui/frontend
docker push "$REG/vc-frontend:latest"

docker build -f source-code/mcp-server/Dockerfile \
  -t "$REG/vc-mcp-vast-tools:latest" source-code/mcp-server
docker push "$REG/vc-mcp-vast-tools:latest"
```

`vc-backend` is both the API and the OpenClaw agent image (no separate agent image).

---

## Block CSI (agents)

Needed for sandbox PVCs (`sandbox.storage_class: vastdata-block`).

**On every worker** (once): load `nvme` + `nvme-tcp`, install `nvme-cli`, ensure `/etc/nvme/hostnqn`.

**In VAST:** register Host NQNs; create a BLOCK view named exactly like `storageclass.subsystem`.

```bash
cp deploy/csi/vast-block-csi.config.example.yaml deploy/csi/vast-block-csi.config.yaml
# fill the copy (gitignored), then:
./deploy/csi/vast-block-csi.sh
kubectl -n vast-block-csi get pods   # block-vast-node-* → 2/2
```

Optional smoke: `./deploy/vast-claw-snapshot-smoke.sh`.

---

## Deploy

Build and push `vc-backend`, `vc-frontend`, and `vc-mcp-vast-tools` (step 5 above) before this. Deploy pulls those tags; it does not build them.

```bash
./deploy/vast-claw-deploy.sh
```

The ingress certificate is `tls.cert_file` and `tls.key_file` in `deploy/config/workflow-ui.config.yaml`. `TLS_CERT_FILE` and `TLS_KEY_FILE` override those paths.

Order inside the script:

1. Model check — `model.base_url` and `model.embed_base_url` must already list the configured model ids. Deploy does not start those servers; bring them up on the NIM host first ([`SELF_HOSTED_NIM.md`](SELF_HOSTED_NIM.md))
2. Agent-Sandbox controller (CRD)
3. Platform — preflight, namespaces, RBAC, NemoClaw policy, OpenShell gateways, UI + Ingress
4. MCP tools (per department)

UI: `https://vastclaw.solutions.vastdata.com` (script prints `/etc/hosts`).
Use the local admin username/password you configured; no default public credentials exist.

**Agents:** set `runtime.mode: kubernetes`, `model.enabled: true`, `sandbox.storage_class: vastdata-block`, then re-run the same deploy command.

**Config change later:** edit the YAML → re-run `./deploy/vast-claw-deploy.sh` (recreates secret, restarts backend).

**Switch NIM endpoint (hosted ↔ self-hosted):** set `model.base_url` / `embed_base_url` (+ `api_key` / `allow_anonymous`) in `deploy/config/workflow-ui.config.yaml` — the single source of truth. The nemoclaw apply renders those into each department's `sandbox-policy.yaml` `nvidia_api` allow-list automatically, so don't edit the policy by hand. Then re-run deploy. See [`config/README.md`](config/README.md).

---

## Verify

```bash
kubectl get ns | grep vastclaw-
kubectl get sc vastdata-block
kubectl get crd sandboxes.agents.x-k8s.io
kubectl -n vastclaw-sales get cm,pods,sandboxes,pvc
./deploy/vast-claw-sanity.sh
```

---

## Cleanup

```bash
./deploy/vast-claw-deploy.sh cleanup       # keeps earnings call_chunks
./deploy/vast-claw-deploy.sh cleanup-all   # drops the corpus too
```

Both tear down MCP → platform (UI, gateways, NemoClaw, RBAC, namespaces) → Agent-Sandbox controller, drop the control, spans, and memory tables, and remove the app buckets and UI logins. `cleanup` leaves the corpus schema, the corpus bucket, the S3 user, and `vast-access.yaml`, so the next deploy sees the loaded rows and skips ingest. `cleanup-all` drops the corpus schema and removes that bucket, the S3 user, and the key file. The tenant, view policy, manager role, and block CSI stay either way.

For CSI: `./deploy/csi/vast-block-csi.sh cleanup`.

---

## How agents fit (short)

| Piece | Role |
| --- | --- |
| OpenShell gateway | `CreateSandbox`. Network endpoints are `enforcement: enforce` |
| NemoClaw | Per-dept blueprint + deny-by-default sandbox policy |
| OpenClaw | Agent loop inside the sandbox on a VAST block PVC |
| VastDB / S3 / VMS | Corpus, spans, memory, and logins. Reached only through the policy allow-list |
| Backend | Plan / fan-out / PVC / synthesize |

More detail: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`nemoclaw/README.md`](nemoclaw/README.md).
