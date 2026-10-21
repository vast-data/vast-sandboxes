# Deployment map

Step-by-step runbook and full prerequisites: **[`deploy/README.md`](../deploy/README.md)**.
Deploy creates the VAST tenant login, buckets, and VastDB tables, then loads corpus rows from a cluster pod when `platform.corpus_load` is true.  
How a run works (storage + OpenShell lifecycle): **[`ARCHITECTURE.md`](ARCHITECTURE.md)**.  
Config keys: **[`deploy/config/README.md`](../deploy/config/README.md)**.

| Layer | Where |
|---|---|
| VAST tenant, users, buckets, VastDB tables | Created by `vast-claw-deploy.sh` from `workflow-ui.config.yaml` |
| Namespaces, RBAC | `deploy/k8s/*.yaml` |
| Block CSI (`vastdata-block`) | `deploy/csi/vast-block-csi.sh` |
| Workflow UI | `deploy/k8s/30-workflow-ui.yaml` + config secret |
| MCP servers | `deploy/k8s/40-mcp-*.yaml` via `vast-claw-tools.sh` |
| Agent-Sandbox controller | `deploy/openshell/agent-sandbox-controller.sh` (**required**) |
| OpenShell gateways | `deploy/openshell/vast-claw-openshell.sh` (**required** — only spawn path) |
| NemoClaw blueprint + policy | `deploy/nemoclaw/` ConfigMaps per department |

**Milestone A** — `runtime.mode: local` (in-process demo, no sandboxes).  
**Milestone B** — `runtime.mode: kubernetes` (OpenShell sandboxes + OpenClaw + MCP).

### Before first deploy

1. Copy `deploy/config/workflow-ui.config.example.yaml` to `workflow-ui.config.yaml` (gitignored) and fill endpoints, `platform.view_policy`, `platform.manager_role`, `platform.s3_group`, and user passwords. Deploy creates the buckets, the S3 user in that group, the UI logins, and the tables.
2. `python3` on the deploy host (preflight is stdlib-only).
3. Build and push the images below. The tags must match `runtime.agent_image`, `runtime.frontend_image`, and `runtime.mcp_image` in the config. Deploy does not build images.
4. Install block CSI once (`deploy/csi/vast-block-csi.sh`). The deploy script installs the Agent-Sandbox controller.

Deploy refuses to apply if preflight fails (`deploy/lib/check_prereqs.sh`):
config keys + k8s objects locally; **VastDB / endpoint probes run
in-cluster** (laptop need not reach data VIPs).

Build and push, then deploy. `REG` is the registry path the cluster pulls from.

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

./deploy/vast-claw-deploy.sh              # models → controller → platform → MCP tools
./deploy/vast-claw-sanity.sh
# cleanup: ./deploy/vast-claw-deploy.sh cleanup       # keeps corpus tables
#           ./deploy/vast-claw-deploy.sh cleanup-all   # drops corpus tables too
```

After rebuilding images (`:latest`), restart:

```bash
kubectl -n vastclaw-observability rollout restart deploy/vastclaw-workflow-ui-backend
kubectl -n vastclaw-observability rollout restart deploy/vastclaw-workflow-ui-frontend
```

Config-only: edit `deploy/config/workflow-ui.config.yaml`, re-run
`./deploy/vast-claw-deploy.sh` (recreates the secret, re-runs preflight,
and rolls the backend).
