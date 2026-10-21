import { useCallback, useEffect, useState } from "react";
import { checkpointWorkflow, restoreVolumeFromSnapshot } from "../../api";
import { usePolling } from "../../hooks/usePolling";
import type { WorkflowStorageState } from "../../types";
import { CubeIcon } from "../icons";

interface Props {
  workflowId: string;
  storage: WorkflowStorageState | null | undefined;
  status: string;
  onChanged: () => void;
}

function roleLabel(role: string): string {
  if (role === "head") return "lead workspace";
  if (role === "subagent") return "openshell sandbox";
  return role || "—";
}

export function StorageView({ workflowId, storage, status, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [local, setLocal] = useState<WorkflowStorageState | null>(storage ?? null);

  useEffect(() => {
    setLocal(storage ?? null);
  }, [storage]);

  const reload = useCallback(() => {
    onChanged();
  }, [onChanged]);

  usePolling(reload, status === "running" ? 6000 : 20000, !!workflowId);

  const view = local ?? storage;

  async function onCheckpoint() {
    setBusy(true);
    setError("");
    try {
      await checkpointWorkflow(workflowId, { label: "manual" });
      onChanged();
    } catch {
      setError("Checkpoint failed — is external-snapshotter installed?");
    } finally {
      setBusy(false);
    }
  }

  async function onRestore(snapshotName: string) {
    setBusy(true);
    setError("");
    try {
      const next = await restoreVolumeFromSnapshot(workflowId, snapshotName);
      setLocal(next);
      onChanged();
    } catch {
      setError("Restore failed — check snapshot is readyToUse.");
    } finally {
      setBusy(false);
    }
  }

  if (!view) {
    return (
      <div className="storage-panel muted">
        Storage telemetry is available when the runtime is in kubernetes mode with block CSI.
      </div>
    );
  }

  const head = view.pvcs.find((p) => p.role === "head") ?? view.pvcs.find((p) => p.name === view.head_pvc);
  const subagents = view.pvcs.filter((p) => p.role === "subagent");
  const restores = view.pvcs.filter((p) => p.role === "restore");

  return (
    <div className="storage-panel">
      <div className="sandbox-banner storage-hero">
        <div className="sandbox-banner-lead">
          <CubeIcon size={18} />
          <div>
            <strong>VAST block + OpenShell</strong>
            <span>
              Live workflow volumes on VAST block CSI — OpenShell mounts each sub-agent PVC
              at spawn (zero-copy clone), with VolumeSnapshot checkpoints and restore.
              Finished runs keep PVCs until you reclaim them (factory reset / cleanup).
            </span>
          </div>
        </div>
        <div className="storage-actions">
          <button
            className="btn"
            disabled={busy}
            onClick={onCheckpoint}
            title="VolumeSnapshot of the lead workspace (head PVC)"
          >
            Checkpoint lead workspace
          </button>
        </div>
      </div>

      {error && <p className="error-text">{error}</p>}

      <div className="storage-grid">
        <section className="storage-card">
          <h3>Primitives</h3>
          <ul className="storage-kv">
            <li>
              <span>Clone on spawn</span>
              <span className={view.clone_enabled ? "on" : ""}>
                {view.clone_enabled ? "enabled" : "off"}
              </span>
            </li>
            <li>
              <span>Pre-fan-out checkpoint</span>
              <span className={view.checkpoint_enabled ? "on" : ""}>
                {view.checkpoint_enabled ? "enabled" : "off"}
              </span>
            </li>
            <li>
              <span>On sub-agent complete</span>
              <span className={view.checkpoint_on_subagent_complete ? "on" : ""}>
                {view.checkpoint_on_subagent_complete ? "enabled" : "off"}
              </span>
            </li>
            <li>
              <span>Wake on continue</span>
              <span
                className={view.wake_on_continue ? "on" : ""}
                title="When sandbox pods are dead, continue remounts retained PVCs or restores from the last VolumeSnapshot"
              >
                {view.wake_on_continue ? "remount / snapshot" : "off"}
              </span>
            </li>
            <li>
              <span>VolumeSnapshotClass</span>
              <code>{view.snapshot_class || "—"}</code>
            </li>
            <li>
              <span>Last checkpoint</span>
              <code>{view.last_checkpoint || "—"}</code>
            </li>
          </ul>
        </section>

        <section className="storage-card">
          <h3>Lead workspace (head PVC)</h3>
          {head ? (
            <>
              <div className="storage-pvc">
                <code>{head.name}</code>
                <span className="tg-chip block">{head.status || "—"}</span>
                <span className="tg-chip">{head.capacity || head.storage_class}</span>
              </div>
              <p className="storage-note muted">
                The run&apos;s shared workspace: seeded before fan-out, snapshotted as the
                pre-fan-out checkpoint, and cloned into every OpenShell sandbox. The lead
                itself orchestrates in the platform — it has no sandbox pod of its own.
              </p>
            </>
          ) : (
            <p className="muted">Lead workspace not provisioned yet.</p>
          )}
        </section>
      </div>

      <section className="storage-card table-card">
        <h3>PVC lineage ({view.pvcs.length})</h3>
        {view.pvcs.length === 0 ? (
          <p className="muted">No volumes yet — created when a workflow runs and fans out sub-agents.</p>
        ) : (
          <div className="storage-table-wrap">
            <table className="storage-table">
              <thead>
                <tr>
                  <th>PVC</th>
                  <th>Role</th>
                  <th>Clone source</th>
                  <th>Class</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {view.pvcs.map((p) => (
                  <tr key={p.name}>
                    <td>
                      <code>{p.name}</code>
                    </td>
                    <td>{roleLabel(p.role)}{p.subagent ? ` (${p.subagent})` : ""}</td>
                    <td>{p.clone_source_pvc ? <code>{p.clone_source_pvc}</code> : "—"}</td>
                    <td>{p.storage_class || "—"}</td>
                    <td>{p.status || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {subagents.length > 0 && (
          <p className="storage-note muted">
            Each OpenShell sandbox PVC is a zero-copy block clone of the lead workspace{" "}
            <code>{view.head_pvc}</code> at spawn time (RWO-safe — the lead workspace and
            OpenShell sandboxes never share a mount).
          </p>
        )}
        {restores.length > 0 && (
          <p className="storage-note muted">
            Restored volumes from VolumeSnapshot checkpoints appear with role <code>restore</code>.
          </p>
        )}
      </section>

      <section className="storage-card table-card">
        <h3>VolumeSnapshots ({view.snapshots.length})</h3>
        {view.snapshots.length === 0 ? (
          <p className="muted">
            Checkpoints appear after pre-fan-out / each sub-agent complete (when enabled), or
            when you click Checkpoint above.
          </p>
        ) : (
          <div className="storage-table-wrap">
            <table className="storage-table">
              <thead>
                <tr>
                  <th>Snapshot</th>
                  <th>Source PVC</th>
                  <th>Ready</th>
                  <th>Run</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {view.snapshots.map((s) => (
                  <tr key={s.name}>
                    <td>
                      <code>{s.name}</code>
                    </td>
                    <td>
                      <code>{s.source_pvc}</code>
                    </td>
                    <td className={s.ready ? "on" : ""}>{s.ready ? "ready" : "pending"}</td>
                    <td>{s.run_id || "—"}</td>
                    <td>
                      <button
                        className="link-btn"
                        disabled={busy || !s.ready}
                        onClick={() => onRestore(s.name)}
                        title="Create a new PVC from this VolumeSnapshot"
                      >
                        Restore from snapshot
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
