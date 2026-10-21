import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clearToken,
  consumeBlueprintsAutoLogin,
  deleteWorkflow,
  getMe,
  getPlatformModel,
  getToken,
  listWorkflows,
} from "./api";
import { AdminView } from "./components/AdminView";
import { Brand } from "./components/Brand";
import { CompareView } from "./components/CompareView";
import { DepartmentDashboard } from "./components/DepartmentDashboard";
import { Home } from "./components/Home";
import { ColumnsIcon, CubeIcon, FlowIcon, HomeIcon, LogoutIcon, PlusIcon, ShieldIcon } from "./components/icons";
import { Login } from "./components/Login";
import { ModelBadge } from "./components/ModelBadge";
import { NewWorkflow } from "./components/NewWorkflow";
import { SettingsMenu } from "./components/SettingsMenu";
import { ListSkeleton } from "./components/Skeletons";
import { WorkflowDetailView } from "./components/WorkflowDetailView";
import { WorkflowList } from "./components/WorkflowList";
import { usePolling } from "./hooks/usePolling";
import { isActive } from "./theme";
import { useToast } from "./toast";
import type { PlatformModelInfo, User, WorkflowSummary } from "./types";
import { onHashChange, readHash, writeHash } from "./urlState";

type Selection = string | "new" | null;
type View = "home" | "workflows" | "admin" | "compare";

const VIEWS: View[] = ["home", "workflows", "admin", "compare"];

const BASE_TITLE = "Secure agents on VAST";

// Run once at module load so the JWT is in localStorage before the boot effect.
consumeBlueprintsAutoLogin();

function flashTitle(text: string) {
  document.title = text;
  const restore = () => {
    document.title = BASE_TITLE;
    window.removeEventListener("focus", restore);
  };
  window.addEventListener("focus", restore);
  window.setTimeout(restore, 6000);
}

export default function App() {
  const toast = useToast();
  const [user, setUser] = useState<User | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [selected, setSelected] = useState<Selection>(null);
  const [view, setView] = useState<View>("home");
  const [booting, setBooting] = useState(true);
  const [platformModel, setPlatformModel] = useState<PlatformModelInfo | null>(null);

  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [deptFilter, setDeptFilter] = useState("");
  const [pendingDelete, setPendingDelete] = useState<WorkflowSummary | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const prevStatus = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (!getToken()) {
      setBooting(false);
      return;
    }
    getMe()
      .then((u) => {
        setUser(u);
        return getPlatformModel().then(setPlatformModel).catch(() => setPlatformModel(null));
      })
      .catch(() => clearToken())
      .finally(() => setBooting(false));
  }, []);

  useEffect(() => {
    const apply = () => {
      const h = readHash();
      setView((VIEWS as string[]).includes(h.view) ? (h.view as View) : "home");
      setSelected(null);
    };
    apply();
    return onHashChange(apply);
  }, []);

  useEffect(() => {
    writeHash({ view: view === "home" ? null : view, wf: null });
  }, [view]);

  const refresh = useCallback(async () => {
    try {
      const items = await listWorkflows();
      setWorkflows(items);
    } catch {
      setUser(null);
    } finally {
      setLoaded(true);
    }
  }, []);

  const confirmDelete = useCallback(async () => {
    if (!pendingDelete) return;
    const { workflow_id: id, root_agent: name } = pendingDelete;
    setDeletingId(id);
    try {
      const res = await deleteWorkflow(id);
      const reaped =
        res.sandboxes.length + res.snapshots.length + res.pvcs.length + res.jobs.length;
      toast.push(
        reaped > 0
          ? `Deleted "${name}" — reaped ${res.pvcs.length} PVC(s), ${res.snapshots.length} snapshot(s), ${res.sandboxes.length} sandbox(es)`
          : `Deleted "${name}"`,
        "success",
      );
      setPendingDelete(null);
      // Deleting the open workflow must drop the detail pane, not leave a dead id.
      setSelected((cur) => (cur === id ? null : cur));
      await refresh();
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "Failed to delete workflow", "error");
    } finally {
      setDeletingId(null);
    }
  }, [pendingDelete, refresh, toast]);

  // Initial load whenever we enter the workflows view.
  useEffect(() => {
    if (user && view === "workflows") void refresh();
  }, [user, view, refresh]);

  const anyActive = useMemo(() => workflows.some((w) => isActive(w.status)), [workflows]);
  usePolling(refresh, anyActive ? 5000 : 20000, Boolean(user) && view === "workflows");

  // Completion notifications: toast + title flash when a run leaves an active state.
  useEffect(() => {
    const prev = prevStatus.current;
    if (prev.size > 0) {
      for (const w of workflows) {
        const before = prev.get(w.workflow_id);
        if (before && isActive(before) && !isActive(w.status)) {
          const failed = w.status === "error";
          toast.push(
            `Workflow "${w.root_agent}" ${failed ? "failed" : "completed"}`,
            failed ? "error" : "success",
          );
          if (document.hidden) flashTitle(`● Workflow ${failed ? "failed" : "done"}`);
        } else if (isActive(w.status) && (before === undefined || !isActive(before))) {
          // A run just entered an active state — a new workflow, or a resumed/continued one.
          toast.push(`Workflow "${w.root_agent}" started`, "info");
        }
      }
    }
    prevStatus.current = new Map(workflows.map((w) => [w.workflow_id, w.status]));
  }, [workflows, toast]);

  const statuses = useMemo(
    () => Array.from(new Set(workflows.map((w) => w.status))).sort(),
    [workflows],
  );
  const departments = useMemo(
    () => Array.from(new Set(workflows.map((w) => w.department))).sort(),
    [workflows],
  );
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return workflows.filter((w) => {
      if (statusFilter && w.status !== statusFilter) return false;
      if (deptFilter && w.department !== deptFilter) return false;
      if (q && !`${w.root_agent} ${w.department} ${w.user} ${w.status}`.toLowerCase().includes(q))
        return false;
      return true;
    });
  }, [workflows, statusFilter, deptFilter, query]);

  if (booting) {
    return (
      <div className="login">
        <span className="spinner" />
      </div>
    );
  }

  if (!user || !getToken()) {
    return (
      <Login
        onAuthenticated={(u: User) => {
          setUser(u);
          getPlatformModel()
            .then(setPlatformModel)
            .catch(() => setPlatformModel(null));
        }}
      />
    );
  }

  function signOut() {
    clearToken();
    setUser(null);
    setWorkflows([]);
    setSelected(null);
    setView("workflows");
  }

  function onCreated(workflowId: string) {
    setSelected(workflowId);
    refresh();
  }

  function onReset() {
    setSelected(null);
    setView("workflows");
    setWorkflows([]);
    refresh();
  }

  function goHome() {
    setSelected(null);
    setView("home");
  }

  function startWorkflow() {
    setView("workflows");
    setSelected("new");
  }

  const filtersActive = Boolean(query || statusFilter || deptFilter);
  const showComposer = selected === "new";
  const showHero = loaded && selected === null && workflows.length === 0;

  return (
    <div className="app">
      <header className="topnav">
        <Brand onClick={goHome} />
        <nav className="nav-tabs">
          <button className={view === "home" ? "active" : ""} onClick={() => setView("home")}>
            <HomeIcon size={15} /> Home
          </button>
          <button
            className={view === "workflows" ? "active" : ""}
            onClick={() => {
              setView("workflows");
              setSelected(null);
            }}
          >
            <FlowIcon size={15} /> Workflows
          </button>
          <button className={view === "compare" ? "active" : ""} onClick={() => setView("compare")}>
            <ColumnsIcon size={15} /> Compare
          </button>
          {user.is_admin && (
            <button className={view === "admin" ? "active" : ""} onClick={() => setView("admin")}>
              <ShieldIcon size={15} /> Admin
            </button>
          )}
        </nav>
        <div className="nav-right">
          <a
            className="btn btn-ghost"
            href="/blueprint.html"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Open platform blueprint"
          >
            <CubeIcon size={15} /> Blueprint
          </a>
          <ModelBadge info={platformModel} />
          <div className="user-chip">
            <div className="u">{user.username}</div>
            <div className="d">
              {user.is_admin
                ? "admin · all departments"
                : user.department_display || user.department}
            </div>
          </div>
          {user.is_admin && <SettingsMenu onReset={onReset} />}
          <button className="btn btn-ghost" onClick={signOut} aria-label="Sign out">
            <LogoutIcon size={15} /> Sign out
          </button>
        </div>
      </header>

      {view === "home" ? (
        <div className="body">
          <Home onStart={startWorkflow} user={user} />
        </div>
      ) : view === "admin" && user.is_admin ? (
        <div className="body">
          <AdminView
            onOpenWorkflow={(workflowId) => {
              setView("workflows");
              setSelected(workflowId);
            }}
          />
        </div>
      ) : view === "compare" ? (
        <div className="body">
          <CompareView workflows={workflows} />
        </div>
      ) : (
        <div className="body">
          <div className="sidebar">
            <div className="sidebar-head">
              <h2>Workflows</h2>
              <button className="btn btn-primary new-btn" onClick={() => setSelected("new")}>
                <PlusIcon size={14} /> New
              </button>
            </div>
            <div className="list-filters">
              <input
                className="filter-search"
                placeholder="Search workflows…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search workflows"
              />
              <div className="filter-row">
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  aria-label="Filter by status"
                >
                  <option value="">All status</option>
                  {statuses.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                {user.is_admin && departments.length > 1 && (
                  <select
                    value={deptFilter}
                    onChange={(e) => setDeptFilter(e.target.value)}
                    aria-label="Filter by department"
                  >
                    <option value="">All depts</option>
                    {departments.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>
            {loaded ? (
              <WorkflowList
                workflows={filtered}
                selected={typeof selected === "string" && selected !== "new" ? selected : null}
                onSelect={setSelected}
                emptyLabel={
                  filtersActive
                    ? "No workflows match these filters."
                    : "No workflows yet. Start a new research."
                }
                user={user}
                onDelete={setPendingDelete}
                deletingId={deletingId}
              />
            ) : (
              <ListSkeleton />
            )}
          </div>
          {showComposer ? (
            <NewWorkflow
              onCreated={onCreated}
              department={user.department_display || user.department || "your department"}
              examplePrompts={user.example_prompts}
            />
          ) : showHero ? (
            <Home onStart={() => setSelected("new")} user={user} />
          ) : selected && selected !== "new" ? (
            <WorkflowDetailView
              workflowId={selected}
              onChanged={refresh}
              onDeleted={() => {
                setSelected(null);
                refresh();
              }}
              user={user}
            />
          ) : (
            <DepartmentDashboard
              user={user}
              onOpen={setSelected}
              onNew={() => setSelected("new")}
            />
          )}
        </div>
      )}

      {pendingDelete && (
        <div
          className="overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Confirm delete workflow"
          onClick={() => !deletingId && setPendingDelete(null)}
        >
          <div className="card modal" onClick={(e) => e.stopPropagation()}>
            <h3>Delete workflow?</h3>
            <p className="muted">
              This permanently deletes <strong>“{pendingDelete.root_agent}”</strong> and
              everything it owns: OpenShell sandboxes, VolumeSnapshots, PVCs (and their PVs),
              the research graph/telemetry, saved reports, and history. This cannot be undone.
            </p>
            {isActive(pendingDelete.status) && (
              <p className="error">
                This workflow is still <strong>{pendingDelete.status}</strong> — deleting it now
                will stop the run and remove its live sandboxes.
              </p>
            )}
            <div className="modal-actions">
              <button
                className="btn"
                onClick={() => setPendingDelete(null)}
                disabled={!!deletingId}
              >
                Cancel
              </button>
              <button className="btn btn-danger" onClick={confirmDelete} disabled={!!deletingId}>
                {deletingId ? "Deleting…" : "Delete workflow"}
              </button>
            </div>
          </div>
        </div>
      )}

      <footer className="footer">
        <span className="footer-brand">Secure agents on VAST</span>
        <span className="footer-tag">
          OpenShell sandboxes on VAST — NemoClaw-governed, retrieval-grounded, observable
        </span>
        <span className="footer-powered">
          Powered by <b>NemoClaw</b> + <b>OpenShell</b> on <b>VAST</b>
        </span>
      </footer>
    </div>
  );
}
