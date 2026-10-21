import { useCallback, useEffect, useMemo, useState } from "react";
import { getAdminStorage } from "../../api";
import type { AdminStorageView, DepartmentStorageInventory, SandboxStat, WorkflowSandboxRow } from "../../types";
import { CubeIcon } from "../icons";

interface Props {
  onOpenWorkflow?: (workflowId: string) => void;
}

export function AdminStoragePanel({ onOpenWorkflow }: Props) {
  const [data, setData] = useState<AdminStorageView | null>(null);
  const [deptFilter, setDeptFilter] = useState<string>("all");
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await getAdminStorage());
      setLoadError("");
    } catch {
      setLoadError("Could not load agent storage inventory — check backend RBAC / kubernetes mode.");
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
    const timer = setInterval(() => load().catch(() => undefined), 8000);
    return () => clearInterval(timer);
  }, [load]);

  const departments = useMemo(
    () => data?.departments.filter((d) => !d.error || d.workflows.length > 0) ?? [],
    [data],
  );

  const visibleDepts = useMemo(() => {
    if (deptFilter === "all") return departments;
    return departments.filter((d) => d.department === deptFilter);
  }, [departments, deptFilter]);

  if (loadError) {
    return (
      <div className="admin-storage">
        <p className="error-text">{loadError}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="admin-storage muted">
        Loading agent storage inventory…
      </div>
    );
  }

  const totals = data.totals;
  const tiles: [string, string | number, string?][] = [
    ["Workflows w/ volumes", totals.workflows],
    ["OpenShell PVCs", totals.pvc_total],
    ["Live OpenShell", totals.active_agents, totals.active_agents > 0 ? "run" : undefined],
    ["Zero-copy clones", totals.cloned_pvcs, totals.cloned_pvcs > 0 ? "ok" : undefined],
    ["VolumeSnapshots", totals.volume_snapshots, totals.volume_snapshots > 0 ? "ok" : undefined],
    ["Snapshots ready", totals.snapshots_ready],
    ["Pending PVCs", totals.pvc_pending, totals.pvc_pending > 0 ? "warn" : undefined],
    ["Provisioned", totals.capacity_human || "0"],
  ];

  return (
    <div className="admin-storage">
      <div className="admin-storage-hero">
        <div className="admin-storage-hero-lead">
          <CubeIcon size={20} />
          <div>
            <strong>OpenShell sandbox storage</strong>
            <span className="muted">
              Live VAST block volumes per research workflow — head PVC, zero-copy OpenShell
              clones at fan-out, and VolumeSnapshot checkpoints. Read-only ops view; open a
              workflow for checkpoint / restore.
            </span>
          </div>
        </div>
        {totals.storage_class && (
          <span className="sc-chip" title="StorageClass in use">
            {totals.storage_class}
          </span>
        )}
      </div>

      <div className="stat-grid sandbox-tiles">
        {tiles.map(([label, value, tone]) => (
          <div className={`card stat-tile ${tone ? `tone-${tone}` : ""}`} key={label}>
            <div className="n">{value}</div>
            <div className="l">{label}</div>
          </div>
        ))}
      </div>

      <div className="admin-storage-toolbar">
        <label className="muted" htmlFor="admin-storage-dept">
          Department
        </label>
        <select
          id="admin-storage-dept"
          value={deptFilter}
          onChange={(e) => setDeptFilter(e.target.value)}
        >
          <option value="all">All departments</option>
          {data.departments.map((d) => (
            <option key={d.department} value={d.department}>
              {d.department} ({d.summary.workflows} workflow{d.summary.workflows === 1 ? "" : "s"})
            </option>
          ))}
        </select>
      </div>

      {visibleDepts.length === 0 ? (
        <div className="card muted admin-storage-empty">
          No agent sandbox volumes yet. PVCs appear once workflows run in kubernetes mode with
          block CSI.
        </div>
      ) : (
        visibleDepts.map((dept) => (
          <DepartmentBlock
            key={dept.department}
            dept={dept}
            onOpenWorkflow={onOpenWorkflow}
          />
        ))
      )}
    </div>
  );
}

function DepartmentBlock({
  dept,
  onOpenWorkflow,
}: {
  dept: DepartmentStorageInventory;
  onOpenWorkflow?: (workflowId: string) => void;
}) {
  const s = dept.summary;
  return (
    <section className="card admin-storage-dept">
      <div className="admin-storage-dept-head">
        <div>
          <h3 style={{ margin: 0 }}>{dept.department}</h3>
          <span className="muted">{dept.namespace}</span>
        </div>
        <DeptSummaryChips summary={s} />
      </div>

      {dept.error && (
        <p className="error-text" style={{ fontSize: 13 }}>
          {dept.error}
        </p>
      )}

      {dept.workflows.length === 0 ? (
        <p className="muted" style={{ margin: "12px 0 0", fontSize: 13 }}>
          No workflow volumes in this namespace yet.
        </p>
      ) : (
        <div className="admin-storage-table-wrap">
          <table className="data-table admin-storage-table">
            <thead>
              <tr>
                <th>Research</th>
                <th>Status</th>
                <th>Live</th>
                <th>PVCs</th>
                <th>Clones</th>
                <th>Snapshots</th>
                <th>Capacity</th>
                <th>Head PVC</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {dept.workflows.map((row) => (
                <WorkflowRow key={row.workflow_id} row={row} onOpen={onOpenWorkflow} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function DeptSummaryChips({ summary }: { summary: SandboxStat }) {
  return (
    <div className="admin-storage-chips">
      <span><b>{summary.workflows}</b> workflows</span>
      <span><b>{summary.pvc_total}</b> PVCs</span>
      {summary.active_agents > 0 && (
        <span className="agent-live">
          <span className="pulse" /> {summary.active_agents} live
        </span>
      )}
      {summary.pvc_pending > 0 && (
        <span className="warn-text">{summary.pvc_pending} pending</span>
      )}
      <span className="muted">{summary.capacity_human} provisioned</span>
    </div>
  );
}

function WorkflowRow({
  row,
  onOpen,
}: {
  row: WorkflowSandboxRow;
  onOpen?: (workflowId: string) => void;
}) {
  const issue =
    row.pvc_pending > 0
      ? `${row.pvc_pending} PVC pending`
      : row.volume_snapshots > 0 && row.snapshots_ready < row.volume_snapshots
        ? `${row.volume_snapshots - row.snapshots_ready} snapshot not ready`
        : "";

  return (
    <tr className={onOpen ? "clickable" : undefined} onClick={() => onOpen?.(row.workflow_id)}>
      <td>
        <div className="admin-wf-title">{row.title || row.workflow_id}</div>
        <code className="admin-wf-id">{row.workflow_id}</code>
      </td>
      <td>
        {row.status ? <span className={`badge ${row.status}`}>{row.status}</span> : "—"}
      </td>
      <td className={row.active_agents > 0 ? "ok-text" : "muted"}>
        {row.active_agents > 0 ? row.active_agents : "—"}
      </td>
      <td>
        {row.pvc_total}
        {row.subagent_pvcs > 0 && (
          <span className="muted"> ({row.subagent_pvcs} sub)</span>
        )}
      </td>
      <td className={row.cloned_pvcs > 0 ? "ok-text" : "muted"}>
        {row.cloned_pvcs > 0 ? row.cloned_pvcs : "—"}
      </td>
      <td>
        {row.volume_snapshots > 0 ? (
          <span className={row.snapshots_ready === row.volume_snapshots ? "ok-text" : "warn-text"}>
            {row.snapshots_ready}/{row.volume_snapshots}
          </span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td>{row.capacity_human || "—"}</td>
      <td>
        <code>{row.head_pvc}</code>
      </td>
      <td className="admin-storage-actions">
        {issue && <span className="warn-text admin-storage-issue">{issue}</span>}
        {onOpen && (
          <button
            type="button"
            className="link-btn"
            onClick={(e) => {
              e.stopPropagation();
              onOpen(row.workflow_id);
            }}
          >
            Open
          </button>
        )}
      </td>
    </tr>
  );
}
