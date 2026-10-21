import { useCallback, useEffect, useState } from "react";
import { getSandboxes, getStats } from "../api";
import { humanBytes } from "../attrs";
import type { AdminStats, SandboxStat, User } from "../types";
import { ChartSwitcher, type Metric } from "./charts";
import { ExpandableText } from "./ExpandableText";
import { FlowIcon, RefreshIcon } from "./icons";

interface Props {
  user: User;
  onOpen: (workflowId: string) => void;
  onNew: () => void;
}

export function DepartmentDashboard({ user, onOpen, onNew }: Props) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [sandboxes, setSandboxes] = useState<SandboxStat[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, sb] = await Promise.all([getStats(), getSandboxes().catch(() => [])]);
      setStats(s);
      setSandboxes(sb);
      setError(null);
    } catch {
      setError("Failed to load statistics");
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 8000);
    return () => clearInterval(timer);
  }, [load]);

  const scopeLabel = user.is_admin
    ? "All departments"
    : user.department_display || user.department || "Your department";

  if (error) {
    return <div className="detail empty">{error}</div>;
  }
  if (!stats) {
    return (
      <div className="detail empty">
        <span className="spinner" />
      </div>
    );
  }

  const tiles: [string, number | string][] = [
    ["Workflows", stats.workflows],
    ["Running", stats.running],
    ["Completed", stats.completed],
    ["Errors", stats.error],
    ["Runs", stats.runs],
    ["Agents served", stats.agents_served],
    ["Avg agents / workflow", stats.avg_agents_per_workflow],
    ["Tool calls", stats.tool_calls],
    ["Reports saved", stats.reports],
    ["Tokens (est.)", stats.tokens_used.toLocaleString()],
  ];

  const activityData: Metric[] = stats.activity_by_day.map((d) => ({
    label: d.name.slice(5) || d.name,
    value: d.count,
  }));
  const agentData: Metric[] = stats.top_agents.map((a) => ({ label: a.name, value: a.tokens }));
  const toolData: Metric[] = stats.tool_breakdown.map((t) => ({ label: t.name, value: t.count }));
  const statusData: Metric[] = [
    { label: "Running", value: stats.running },
    { label: "Completed", value: stats.completed },
    { label: "Error", value: stats.error },
  ].filter((s) => s.value > 0);

  return (
    <div className="dashboard">
      <div className="dash-head">
        <div>
          <h1>{scopeLabel} · overview</h1>
          <p className="muted">
            Live statistics from VastDB — workflows, agents, tool usage and tokens.
          </p>
        </div>
        <div className="dash-actions">
          <button className="btn btn-primary" onClick={onNew}>
            <FlowIcon size={15} /> New research
          </button>
          <button className="btn btn-ghost" onClick={load} title="Refresh">
            <RefreshIcon size={15} /> Refresh
          </button>
        </div>
      </div>

      <div className="stat-grid">
        {tiles.map(([label, value]) => (
          <div className="card stat-tile" key={label}>
            <div className="n">{value}</div>
            <div className="l">{label}</div>
          </div>
        ))}
      </div>

      <SandboxSection sandboxes={sandboxes} />

      <div className="panel-grid">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Workflows started per day</h3>
          {activityData.length ? (
            <ChartSwitcher data={activityData} defaultType="line" />
          ) : (
            <p className="muted">No workflows yet.</p>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Status breakdown</h3>
          {statusData.length ? (
            <ChartSwitcher data={statusData} defaultType="donut" types={["donut", "bar"]} />
          ) : (
            <p className="muted">No workflows yet.</p>
          )}
        </div>
      </div>

      <div className="panel-grid" style={{ marginTop: 18 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Top agents by tokens</h3>
          {agentData.length ? (
            <ChartSwitcher data={agentData} />
          ) : (
            <p className="muted">No agent activity yet.</p>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Tool usage</h3>
          {toolData.length ? (
            <ChartSwitcher data={toolData} defaultType="donut" />
          ) : (
            <p className="muted">No tool calls yet.</p>
          )}
        </div>
      </div>

      <div className="panel-grid" style={{ marginTop: 18 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Recent activity</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Research</th>
                <th>User</th>
                {user.is_admin && <th>Dept</th>}
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent.map((r, i) => (
                <tr
                  key={`${r.workflow_id}-${i}`}
                  className="clickable"
                  onClick={() => onOpen(r.workflow_id)}
                >
                  <td>{r.title}</td>
                  <td>{r.user}</td>
                  {user.is_admin && <td>{r.department}</td>}
                  <td>
                    <span className={`badge ${r.status}`}>{r.status}</span>
                  </td>
                </tr>
              ))}
              {stats.recent.length === 0 && (
                <tr>
                  <td colSpan={user.is_admin ? 4 : 3} className="muted">
                    No activity yet — start a new research.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>People</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>User</th>
                {user.is_admin && <th>Dept</th>}
                <th>Workflows</th>
                <th>Last active</th>
              </tr>
            </thead>
            <tbody>
              {stats.top_users.map((u) => (
                <tr key={u.username}>
                  <td>{u.username}</td>
                  {user.is_admin && <td>{u.department}</td>}
                  <td>{u.workflows}</td>
                  <td className="muted">{u.last_active ? u.last_active.slice(0, 10) : "—"}</td>
                </tr>
              ))}
              {stats.top_users.length === 0 && (
                <tr>
                  <td colSpan={user.is_admin ? 4 : 3} className="muted">
                    No users yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function SandboxSection({ sandboxes }: { sandboxes: SandboxStat[] }) {
  const agg = sandboxes.reduce(
    (a, s) => ({
      pvc_total: a.pvc_total + s.pvc_total,
      pvc_bound: a.pvc_bound + s.pvc_bound,
      pvc_pending: a.pvc_pending + s.pvc_pending,
      subagent_pvcs: a.subagent_pvcs + s.subagent_pvcs,
      cloned_pvcs: a.cloned_pvcs + s.cloned_pvcs,
      restored_pvcs: a.restored_pvcs + s.restored_pvcs,
      volume_snapshots: a.volume_snapshots + s.volume_snapshots,
      snapshots_ready: a.snapshots_ready + s.snapshots_ready,
      active_agents: a.active_agents + s.active_agents,
      capacity_bytes: a.capacity_bytes + s.capacity_bytes,
    }),
    {
      pvc_total: 0,
      pvc_bound: 0,
      pvc_pending: 0,
      subagent_pvcs: 0,
      cloned_pvcs: 0,
      restored_pvcs: 0,
      volume_snapshots: 0,
      snapshots_ready: 0,
      active_agents: 0,
      capacity_bytes: 0,
    },
  );
  const hasData = sandboxes.some((s) => !s.error);
  const capacityHuman = humanBytes(agg.capacity_bytes);

  const tiles: [string, string | number, string?][] = [
    ["Volumes (PVC)", agg.pvc_total],
    ["Zero-copy clones", agg.cloned_pvcs, agg.cloned_pvcs > 0 ? "ok" : undefined],
    ["VolumeSnapshots", agg.volume_snapshots, agg.volume_snapshots > 0 ? "ok" : undefined],
    ["Snapshots ready", agg.snapshots_ready, agg.snapshots_ready > 0 ? "run" : undefined],
    ["Bound", agg.pvc_bound, "ok"],
    ["Pending", agg.pvc_pending, agg.pvc_pending > 0 ? "warn" : undefined],
    ["OpenShell sandboxes", agg.subagent_pvcs],
    ["Restored PVCs", agg.restored_pvcs, agg.restored_pvcs > 0 ? "ok" : undefined],
    ["Active OpenShell pods", agg.active_agents, agg.active_agents > 0 ? "run" : undefined],
    ["Provisioned capacity", capacityHuman],
  ];

  return (
    <div className="sandbox-section">
      <div className="section-head">
        <h3>OpenShell sandboxes, clones &amp; snapshots</h3>
        <span className="muted">
          Live VAST block CSI volumes — one lead workspace per workflow, plus a zero-copy
          clone mounted into each OpenShell sandbox at fan-out, with VolumeSnapshot
          checkpoints. The research-lead orchestrates in the platform and has no sandbox
          pod.
        </span>
      </div>

      <div className="stat-grid sandbox-tiles">
        {tiles.map(([label, value, tone]) => (
          <div className={`card stat-tile ${tone ? `tone-${tone}` : ""}`} key={label}>
            <div className="n">{value}</div>
            <div className="l">{label}</div>
          </div>
        ))}
      </div>

      <div className="sandbox-grid">
        {sandboxes.map((s) => (
          <div className="card sandbox-card" key={s.department}>
            <div className="sandbox-card-head">
              <strong>{s.department}</strong>
              <span className="muted">· {s.namespace}</span>
              {s.active_agents > 0 && (
                <span className="agent-live">
                  <span className="pulse" /> {s.active_agents} agent{s.active_agents === 1 ? "" : "s"} live
                </span>
              )}
            </div>
            {s.error ? (
              <div className="muted" style={{ fontSize: 12 }}>
                No sandbox data
                <ExpandableText text={s.error} lines={2} />
              </div>
            ) : (
              <>
                <div className="pvc-bar" title={`${s.pvc_bound} bound · ${s.pvc_pending} pending of ${s.pvc_total}`}>
                  <span
                    className="pvc-bound"
                    style={{ width: `${s.pvc_total ? (s.pvc_bound / s.pvc_total) * 100 : 0}%` }}
                  />
                  <span
                    className="pvc-pending"
                    style={{ width: `${s.pvc_total ? (s.pvc_pending / s.pvc_total) * 100 : 0}%` }}
                  />
                </div>
                <div className="sandbox-meta">
                  <span><b>{s.pvc_total}</b> PVCs</span>
                  <span className="ok-text">{s.pvc_bound} bound</span>
                  {s.pvc_pending > 0 && <span className="warn-text">{s.pvc_pending} pending</span>}
                  <span>· {s.head_pvcs} lead workspace / {s.subagent_pvcs} OpenShell</span>
                  <span>· {s.workflows} workflows</span>
                </div>
                <div className="sandbox-meta storage-metrics">
                  {s.cloned_pvcs > 0 && (
                    <span className="storage-chip clone" title="Sub-agent PVCs forked from workflow head">
                      <b>{s.cloned_pvcs}</b> cloned
                    </span>
                  )}
                  {s.volume_snapshots > 0 && (
                    <span className="storage-chip snap" title="VolumeSnapshot checkpoints">
                      <b>{s.snapshots_ready}</b>/{s.volume_snapshots} snapshots ready
                    </span>
                  )}
                  {s.restored_pvcs > 0 && (
                    <span className="storage-chip restore" title="PVCs restored from snapshot">
                      <b>{s.restored_pvcs}</b> restored
                    </span>
                  )}
                  {(s.cloned_pvcs === 0 && s.volume_snapshots === 0) && (
                    <span className="muted">no clone/snapshot activity yet</span>
                  )}
                </div>
                <div className="sandbox-meta">
                  <span>{humanBytes(s.capacity_bytes)} provisioned</span>
                  {s.storage_class && (
                    <span className="sc-chip" title="StorageClass">{s.storage_class}</span>
                  )}
                </div>
              </>
            )}
          </div>
        ))}
        {(!hasData && sandboxes.length === 0) && (
          <div className="card muted" style={{ fontSize: 13 }}>
            No OpenShell telemetry yet. Volumes appear here once a workflow fans out
            sandboxes through the OpenShell gateway (runtime.mode: kubernetes).
          </div>
        )}
      </div>
    </div>
  );
}
