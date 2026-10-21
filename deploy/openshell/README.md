# OpenShell gateways

Per-department OpenShell is the **only** sub-agent spawn path.

| Script | Purpose |
|---|---|
| `agent-sandbox-controller.sh` | Install Sandbox CRD (once per cluster) |
| `vast-claw-openshell.sh` | Install/upgrade/cleanup gateways (`os-sales`, `os-finance`) |
| `../config/openshell.config.yaml` | Chart version + department list |
| `values.yaml` | E2E Helm values (plaintext gateway for lab) |

Design / lifecycle: [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).  
Blueprint + policy + hard constraints: [`../nemoclaw/README.md`](../nemoclaw/README.md).

```bash
./deploy/openshell/agent-sandbox-controller.sh
./deploy/openshell/vast-claw-openshell.sh          # or via vast-claw-deploy.sh
./deploy/openshell/vast-claw-openshell.sh cleanup  # full teardown (PVCs + ClusterRoles)
```

Gateway Service DNS: `os-<dept>.vastclaw-<dept>.svc.cluster.local:8080`
(must match `agent_sandbox.gateway_endpoint_template`).
