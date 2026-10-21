import { useCallback, useEffect, useState } from "react";
import {
  getAdminAudit,
  getAdminHealth,
  getAdminStats,
  getAdminUsers,
  restartDepartment,
  restartPlatform,
} from "../api";
import type { AdminAction, AdminStats, DepartmentHealth, UserStat } from "../types";
import { AdminStoragePanel } from "./admin/AdminStoragePanel";
import { RefreshIcon } from "./icons";

interface Confirm {
  title: string;
  body: string;
  run: () => Promise<void>;
}

type AdminTab = "platform" | "storage";

interface Props {
  onOpenWorkflow?: (workflowId: string) => void;
}

export function AdminView({ onOpenWorkflow }: Props) {
  const [tab, setTab] = useState<AdminTab>("platform");
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [health, setHealth] = useState<DepartmentHealth[]>([]);
  const [users, setUsers] = useState<UserStat[]>([]);
  const [audit, setAudit] = useState<AdminAction[]>([]);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [s, h, u, a] = await Promise.all([
      getAdminStats(),
      getAdminHealth(),
      getAdminUsers(),
      getAdminAudit(),
    ]);
    setStats(s);
    setHealth(h);
    setUsers(u);
    setAudit(a);
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
    const timer = setInterval(() => load().catch(() => undefined), 6000);
    return () => clearInterval(timer);
  }, [load]);

  async function runConfirmed() {
    if (!confirm) return;
    setBusy(true);
    setNotice(null);
    try {
      await confirm.run();
      setNotice("Restart triggered. Pods are rolling.");
      await load();
    } catch {
      setNotice("Restart failed. Check backend RBAC / cluster access.");
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  const tiles: [string, number | string][] = stats
    ? [
        ["Workflows", stats.workflows],
        ["Running", stats.running],
        ["Completed", stats.completed],
        ["Errors", stats.error],
        ["Runs", stats.runs],
        ["Agents served", stats.agents_served],
        ["Tool calls", stats.tool_calls],
        ["Tokens (est.)", stats.tokens_used.toLocaleString()],
      ]
    : [];

  return (
    <div className="admin">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1>Platform admin</h1>
          <p className="muted">
            {tab === "platform"
              ? "Live platform statistics, department health and controls."
              : "Agent sandbox volumes, clones, and snapshots across all departments."}
          </p>
        </div>
        {tab === "platform" && (
          <button
            className="btn btn-danger"
            onClick={() =>
              setConfirm({
                title: "Restart entire platform?",
                body: "This rolls all deployments in every vastclaw-<dept> namespace. In-flight runs may be interrupted.",
                run: async () => {
                  await restartPlatform();
                },
              })
            }
          >
            <RefreshIcon size={15} /> Restart platform
          </button>
        )}
      </div>

      <div className="admin-tabs">
        <button
          type="button"
          className={tab === "platform" ? "active" : ""}
          onClick={() => setTab("platform")}
        >
          Platform
        </button>
        <button
          type="button"
          className={tab === "storage" ? "active" : ""}
          onClick={() => setTab("storage")}
        >
          Agent storage
        </button>
      </div>

      {tab === "storage" ? (
        <AdminStoragePanel onOpenWorkflow={onOpenWorkflow} />
      ) : (
        <>

      {notice && (
        <div className="card" style={{ marginTop: 14, padding: 12 }}>
          {notice}
        </div>
      )}

      <div className="stat-grid">
        {tiles.map(([label, value]) => (
          <div className="card stat-tile" key={label}>
            <div className="n">{value}</div>
            <div className="l">{label}</div>
          </div>
        ))}
      </div>

      <div className="panel-grid">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Departments</h3>
          {health.map((h) => {
            const ds = stats?.by_department.find((d) => d.department === h.department);
            return (
              <div className="dept-card" key={h.department} style={{ marginBottom: 18 }}>
                <div className="dept-head">
                  <div>
                    <span className={`dot ${h.healthy ? "up" : "down"}`} />
                    <strong>{h.department}</strong>{" "}
                    <span className="muted">· {h.namespace}</span>
                  </div>
                  <button
                    className="btn btn-danger"
                    onClick={() =>
                      setConfirm({
                        title: `Restart ${h.department}?`,
                        body: `This rolls all deployments in ${h.namespace}. In-flight runs for this department may be interrupted.`,
                        run: async () => {
                          await restartDepartment(h.department);
                        },
                      })
                    }
                  >
                    <RefreshIcon size={14} /> Restart
                  </button>
                </div>
                {ds && (
                  <div className="wf-meta">
                    <span>{ds.workflows} workflows</span>
                    <span>· {ds.agents} agents</span>
                    <span>· {ds.tokens.toLocaleString()} tokens (est.)</span>
                  </div>
                )}
                {h.error ? (
                  <div className="error">{h.error}</div>
                ) : (
                  h.deployments.map((d) => (
                    <div className="health-row" key={d.name}>
                      <span>{d.name}</span>
                      <span className={d.ready >= d.desired && d.desired > 0 ? "" : "error"}>
                        {d.ready}/{d.desired} ready
                      </span>
                    </div>
                  ))
                )}
                {!h.error && h.deployments.length === 0 && (
                  <div className="muted">No deployments found.</div>
                )}
              </div>
            );
          })}
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>Recent activity</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Research</th>
                <th>User</th>
                <th>Dept</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(stats?.recent ?? []).map((r, i) => (
                <tr key={`${r.workflow_id}-${i}`}>
                  <td>{r.title}</td>
                  <td>{r.user}</td>
                  <td>{r.department}</td>
                  <td>
                    <span className={`badge ${r.status}`}>{r.status}</span>
                  </td>
                </tr>
              ))}
              {(stats?.recent ?? []).length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No activity yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel-grid" style={{ marginTop: 18 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Users</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Dept</th>
                <th>Role</th>
                <th>Workflows</th>
                <th>Last active</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.username}>
                  <td>{u.username}</td>
                  <td>{u.department}</td>
                  <td>{u.is_admin ? "admin" : "member"}</td>
                  <td>{u.workflows}</td>
                  <td className="muted">{u.last_active ? u.last_active.slice(0, 10) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>Admin audit log</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Target</th>
              </tr>
            </thead>
            <tbody>
              {audit.map((a) => (
                <tr key={a.action_id}>
                  <td className="muted">{a.ts.slice(0, 19).replace("T", " ")}</td>
                  <td>{a.actor}</td>
                  <td>{a.action}</td>
                  <td className="muted">{a.target}</td>
                </tr>
              ))}
              {audit.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No admin actions recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
        </>
      )}

      {confirm && (
        <div className="overlay" onClick={() => !busy && setConfirm(null)}>
          <div className="card modal" onClick={(e) => e.stopPropagation()}>
            <h3>{confirm.title}</h3>
            <p className="muted">{confirm.body}</p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setConfirm(null)} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-danger" onClick={runConfirmed} disabled={busy}>
                {busy ? "Restarting…" : "Confirm restart"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
