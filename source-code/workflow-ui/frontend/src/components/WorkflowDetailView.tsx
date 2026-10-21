import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "reactflow";
import "reactflow/dist/style.css";
import { TurboDefs } from "./graph/TurboDefs";
import { TurboEdge } from "./graph/TurboEdge";
import { TurboNode } from "./graph/TurboNode";
import {
  addTurn,
  checkpointWorkflow,
  createReport,
  deleteWorkflow,
  getPlatformModel,
  getReportBlob,
  listReports,
  listTools,
} from "../api";
import {
  type Highlight,
  artifacts,
  aggregateEvidence,
  citationsForSource,
  cleanSummary,
  customerActions,
  isSubAgent,
  jsonArr,
  metrics,
  runCost,
  shortModelName,
  summaryCitations,
  workflowProducedBy,
  evidenceByAgent,
} from "../attrs";
import { useWorkflowDetail } from "../hooks/useWorkflowDetail";
import { useWorkflowNotifications } from "../hooks/useWorkflowNotifications";
import { displayFor, prettySpanLabel, runWokeVolumes, toFlow } from "../layout";
import { GRAPH, isActive } from "../theme";
import { useToast } from "../toast";
import type {
  Evidence,
  PlatformModelInfo,
  QaMessage,
  ReportMeta,
  ToolInfo,
  User,
  WorkflowDetail,
  GraphNode,
} from "../types";
import { readHash, writeHash } from "../urlState";
import { ChartSwitcher } from "./charts";
import { ExpandableText } from "./ExpandableText";
import { ArtifactsView } from "./detail/ArtifactsView";
import { StorageView } from "./detail/StorageView";
import { CustomerActions } from "./detail/CustomerActions";
import { DecisionsView, SubAgentCard } from "./detail/DecisionsView";
import { ExpandableCitations } from "./detail/ExpandableCitations";
import { VastDbCitations } from "./detail/VastDbCitations";
import { NodeInspector } from "./detail/Inspector";
import { LiveLogPane } from "./detail/LiveLogPane";
import { PlanApproval } from "./detail/PlanApproval";
import { CapabilitiesBar, CostStrip, LiveStatus } from "./detail/Panels";
import { SendIcon, TrashIcon } from "./icons";
import { Markdown } from "./Markdown";
import { ModelBadge } from "./ModelBadge";
import { DetailSkeleton } from "./Skeletons";

// Stable references so React Flow doesn't warn about new type maps each render.
const NODE_TYPES = { turbo: TurboNode };
const EDGE_TYPES = { turbo: TurboEdge };
const EMPTY_NODES: GraphNode[] = [];

type Tab =
  | "graph"
  | "decisions"
  | "timeline"
  | "summary"
  | "qa"
  | "artifacts"
  | "storage"
  | "turns";

const TAB_IDS: Tab[] = [
  "graph",
  "decisions",
  "timeline",
  "summary",
  "qa",
  "artifacts",
  "storage",
  "turns",
];

function isTab(v: string | undefined): v is Tab {
  return !!v && (TAB_IDS as string[]).includes(v);
}

const TRANSCRIPT_MCP_TOOLS = ["transcript_search", "similar_calls", "list_call_metadata"];

function configuredToolNames(department: string, user: User | null): string[] {
  if (user?.department === department && user.mcp_tools?.length) {
    return user.mcp_tools;
  }
  if (department === "sales" || department === "finance") return TRANSCRIPT_MCP_TOOLS;
  return [];
}

interface Props {
  workflowId: string;
  onChanged: () => void;
  onDeleted?: () => void;
  user: User | null;
}

export function WorkflowDetailView({ workflowId, onChanged, onDeleted, user }: Props) {
  const toast = useToast();
  const { detail, setDetail, error, reload } = useWorkflowDetail(workflowId);

  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [qaPrompt, setQaPrompt] = useState("");
  const [qaBusy, setQaBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tab, setTab] = useState<Tab>(() => {
    const t = readHash().tab;
    return isTab(t) ? t : "graph";
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const [checkpointing, setCheckpointing] = useState(false);
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [savingReport, setSavingReport] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [platformModel, setPlatformModel] = useState<PlatformModelInfo | null>(null);
  // Tracks the mounted workflow so switching workflows can reset the tab (below).
  const shownWfRef = useRef(workflowId);

  const reportTab: Tab = "summary";

  const { summaryUnseen, markSummarySeen } = useWorkflowNotifications(
    detail,
    tab,
    true,
    reportTab,
  );

  useEffect(() => {
    if (tab === reportTab) markSummarySeen();
  }, [tab, reportTab, markSummarySeen]);

  useEffect(() => {
    setSelectedId(null);
    setExpanded([]);
    listReports(workflowId)
      .then(setReports)
      .catch(() => setReports([]));
    const dept = detail?.workflow.department;
    if (!dept) return;
    const fallback = configuredToolNames(dept, user);
    if (fallback.length) {
      setTools(fallback.map((name) => ({ name, description: "department MCP tool" })));
    }
    listTools(dept)
      .then((live) => {
        if (live.length > 0) setTools(live);
      })
      .catch(() => {
        /* keep fallback */
      });
    getPlatformModel()
      .then(setPlatformModel)
      .catch(() => setPlatformModel(null));
  }, [workflowId, detail?.workflow.department, user]);

  function selectTab(next: Tab) {
    setTab(next);
    writeHash({ tab: next });
  }

  useEffect(() => {
    if (shownWfRef.current === workflowId) return;
    shownWfRef.current = workflowId;
    setTab("graph");
    writeHash({ tab: "graph" });
  }, [workflowId]);

  async function saveReport() {
    setSavingReport(true);
    try {
      await createReport(workflowId);
      setReports(await listReports(workflowId));
      toast.push("Report saved as PDF", "success");
    } catch {
      toast.push("Failed to save report", "error");
    } finally {
      setSavingReport(false);
    }
  }

  async function openReport(reportId: string, download: boolean) {
    try {
      const blob = await getReportBlob(workflowId, reportId);
      const url = URL.createObjectURL(blob);
      if (download) {
        const link = document.createElement("a");
        link.href = url;
        link.download = `${reportId}.pdf`;
        link.click();
      } else {
        window.open(url, "_blank", "noopener");
      }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      toast.push("Could not open report", "error");
    }
  }

  const graph = detail?.graph;
  const nodes = graph?.nodes ?? EMPTY_NODES;

  const flow = useMemo(
    () => (graph ? toFlow(graph, selectedId) : { nodes: [] as Node[], edges: [] as Edge[] }),
    [graph, selectedId],
  );

  const summaryNode = useMemo(
    () => nodes.find((n) => n.attributes.summary),
    [nodes],
  );
  const planNode = useMemo(() => nodes.find((n) => n.attributes.plan_json), [nodes]);
  const subAgents = useMemo(() => nodes.filter(isSubAgent), [nodes]);
  const evByAgent = useMemo(() => evidenceByAgent(subAgents), [subAgents]);

  const storedCitations = useMemo(() => summaryCitations(summaryNode), [summaryNode]);
  const recommendationCitations = useMemo(() => {
    if (storedCitations.length > 0) return storedCitations;
    return aggregateEvidence(subAgents);
  }, [storedCitations, subAgents]);
  const producedBy = workflowProducedBy(nodes, platformModel?.model ?? "");
  const producedByLabel = producedBy ? shortModelName(producedBy) : "";

  if (error && !detail) return <div className="detail empty error">{error}</div>;
  if (!detail) return <DetailSkeleton />;

  const { workflow, turns, storage, wake_hint } = detail;
  const qaMessages = detail.qa_messages ?? [];
  const willWake = !!wake_hint?.continue_will_wake;
  const canDelete = !!user && (user.is_admin || workflow.created_by === user.username);
  const runActive = isActive(workflow.status);
  const configuredToolFallback = configuredToolNames(workflow.department, user);
  const cost = runCost(nodes);
  const activity = [...nodes].sort((a, b) => a.start_time.localeCompare(b.start_time));
  const artifactCount = subAgents.reduce((n, x) => n + artifacts(x).length, 0);
  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;
  const highlights = jsonArr<Highlight>(summaryNode?.attributes.highlights_json);
  const custActions = customerActions(summaryNode);
  const recommendation = summaryNode?.attributes.recommendation ?? "";
  const byAgent = new Map(subAgents.map((n) => [n.agent_name, n]));
  const metricSections = subAgents
    .map((n) => ({ name: n.agent_name, metrics: metrics(n) }))
    .filter((s) => s.metrics.length > 0);
  // Graph/Summary are scoped to last_run_id (one turn), not a stacked multi-turn DAG.
  const currentTurn =
    turns.find((t) => t.run_id && t.run_id === workflow.last_run_id) ??
    turns[turns.length - 1] ??
    null;
  const turnNumber = currentTurn?.seq ?? turns.length;
  const wokeThisTurn = runWokeVolumes(nodes);

  function toggle(name: string) {
    setExpanded((prev) => (prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]));
  }

  function showAgent(name: string) {
    setExpanded((prev) => (prev.includes(name) ? prev : [...prev, name]));
    const node = byAgent.get(name);
    if (node) setSelectedId(node.id);
  }

  function onPlanApproved(data: WorkflowDetail) {
    setDetail(data);
    onChanged();
  }

  async function doDelete() {
    setDeleting(true);
    try {
      const res = await deleteWorkflow(workflowId);
      const reaped =
        res.sandboxes.length + res.snapshots.length + res.pvcs.length + res.jobs.length;
      toast.push(
        reaped > 0
          ? `Workflow deleted — reaped ${res.pvcs.length} PVC(s), ${res.snapshots.length} snapshot(s), ${res.sandboxes.length} sandbox(es)`
          : "Workflow deleted",
        "success",
      );
      setConfirmDelete(false);
      (onDeleted ?? onChanged)();
    } catch (e) {
      toast.push(e instanceof Error ? e.message : "Failed to delete workflow", "error");
      setDeleting(false);
    }
  }

  async function continueWorkflow(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim()) return;
    const waking = !!wake_hint?.continue_will_wake;
    setBusy(true);
    try {
      const data = await addTurn(workflowId, prompt.trim());
      setDetail(data);
      setPrompt("");
      onChanged();
      if (waking) {
        toast.push(
          "Continuing — remounting retained block volumes (or restoring from last checkpoint).",
          "info",
        );
      }
    } catch {
      toast.push("Could not continue the research.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function askQuestion(event: FormEvent) {
    event.preventDefault();
    if (!qaPrompt.trim()) return;
    setQaBusy(true);
    try {
      const data = await addTurn(workflowId, qaPrompt.trim(), "qa");
      setDetail(data);
      setQaPrompt("");
      onChanged();
    } catch {
      toast.push("Could not send the question.", "error");
    } finally {
      setQaBusy(false);
    }
  }

  const storageAvailable = storage != null;

  async function checkpoint() {
    setCheckpointing(true);
    try {
      const snap = await checkpointWorkflow(workflowId, { label: "manual" });
      await reload();
      toast.push(`Block checkpoint created: ${snap.name}`, "success");
      selectTab("storage");
    } catch {
      toast.push("Checkpoint failed — is block CSI + snapshotter installed?", "error");
    } finally {
      setCheckpointing(false);
    }
  }

  const onNodeClick: NodeMouseHandler = (_e, node) => setSelectedId(node.id);

  const tabs: [Tab, string][] = [
    ["graph", "Graph"],
    ["decisions", "Decisions"],
    ["timeline", "Timeline"],
    ["summary", "Summary"],
    ["qa", qaMessages.length ? `Quick Q&A (${qaMessages.length})` : "Quick Q&A"],
    ["artifacts", artifactCount ? `Artifacts (${artifactCount})` : "Artifacts"],
    [
      "storage",
      storage?.snapshots.length
        ? `Storage (${storage.snapshots.length} snap)`
        : "Storage",
    ],
    ["turns", `History (${turns.length})`],
  ];

  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <h2>{workflow.title}</h2>
          <div className="wf-meta">
            <span className={`badge ${workflow.status}`}>{workflow.status}</span>
            <span>{workflow.department}</span>
            {turnNumber > 0 && (
              <span
                className="turn-chip"
                title={
                  workflow.last_run_id
                    ? `Graph and Summary show turn #${turnNumber} only (${workflow.last_run_id})`
                    : `Turn #${turnNumber}`
                }
              >
                Turn #{turnNumber}
                {turns.length > 1 ? ` of ${turns.length}` : ""}
              </span>
            )}
            {wokeThisTurn && (
              <span className="wake-chip" title="This turn remounted retained volumes after sandboxes slept">
                Woke volumes
              </span>
            )}
            {platformModel && <ModelBadge info={platformModel} compact title="Active platform model" />}
            <span title={workflow.sandbox_view_path}>pvc: {workflow.pvc_name}</span>
          </div>
        </div>
        <div className="detail-actions">
          {storageAvailable && (
            <button
              className="btn btn-ghost"
              onClick={checkpoint}
              disabled={checkpointing}
              title="VolumeSnapshot of the workflow head PVC (block rollback)"
            >
              {checkpointing ? "Checkpointing…" : "Checkpoint volume"}
            </button>
          )}
          {canDelete && (
            <button
              className="btn btn-danger"
              onClick={() => setConfirmDelete(true)}
              disabled={deleting}
              title="Delete this workflow and everything it owns (sandboxes, snapshots, PVCs)"
            >
              <TrashIcon size={14} /> Delete
            </button>
          )}
        </div>
      </div>

      {confirmDelete && (
        <div
          className="overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Confirm delete workflow"
          onClick={() => !deleting && setConfirmDelete(false)}
        >
          <div className="card modal" onClick={(e) => e.stopPropagation()}>
            <h3>Delete workflow?</h3>
            <p className="muted">
              This permanently deletes <strong>“{workflow.title}”</strong> and everything it
              owns: OpenShell sandboxes, VolumeSnapshots, PVCs (and their PVs), the research
              graph/telemetry, saved reports, and history. This cannot be undone.
            </p>
            {runActive && (
              <p className="error">
                This workflow is still <strong>{workflow.status}</strong> — deleting it now will
                stop the run and remove its live sandboxes.
              </p>
            )}
            <div className="modal-actions">
              <button
                className="btn"
                onClick={() => setConfirmDelete(false)}
                disabled={deleting}
              >
                Cancel
              </button>
              <button className="btn btn-danger" onClick={doDelete} disabled={deleting}>
                {deleting ? "Deleting…" : "Delete workflow"}
              </button>
            </div>
          </div>
        </div>
      )}

      <LiveStatus nodes={nodes} status={workflow.status} cost={cost} />

      {workflow.status === "awaiting_approval" && (
        <PlanApproval
          planNode={planNode ?? null}
          workflowId={workflowId}
          onApproved={onPlanApproved}
        />
      )}

      <div className="tabbar" role="tablist" aria-label="Workflow views">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? "active" : ""}
            onClick={() => selectTab(id)}
          >
            {label}
            {id === reportTab && summaryUnseen && (
              <span className="tab-badge" aria-label="New synthesis ready" />
            )}
          </button>
        ))}
      </div>

      {tab === "graph" && (
        <>
          <CapabilitiesBar tools={tools} fallbackNames={configuredToolFallback} />
          <CostStrip cost={cost} />
          {turns.length > 1 && (
            <p className="turn-scope muted">
              Showing turn #{turnNumber}
              {wokeThisTurn ? " · woke volumes" : ""} — prior turns stay in History; the graph is
              not stacked.
            </p>
          )}
        </>
      )}

      {tab === "graph" && (
        <div className="detail-body">
          <LiveLogPane
            nodes={nodes}
            status={workflow.status}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <div className="graph turbo">
            {nodes.length ? (
              <ReactFlow
                nodes={flow.nodes}
                edges={flow.edges}
                nodeTypes={NODE_TYPES}
                edgeTypes={EDGE_TYPES}
                onNodeClick={onNodeClick}
                fitView
              >
                <TurboDefs />
                <Background color={GRAPH.turboDot} gap={26} size={1} />
                <Controls />
              </ReactFlow>
            ) : (
              <div className="graph-empty">
                {workflow.status === "running"
                  ? "Agents are working… nodes and logs appear as steps complete."
                  : "The agent DAG appears here once the workflow emits telemetry to VAST."}
              </div>
            )}
          </div>
          <NodeInspector node={selectedNode} />
        </div>
      )}

      {tab === "decisions" && <DecisionsView planNode={planNode ?? null} subAgents={subAgents} />}

      {tab === "timeline" && (
        <div className="timeline">
          {activity.length === 0 && <p className="muted">No steps yet.</p>}
          <ol>
            {activity.map((n) => {
              const { title, subline } = displayFor(n, false);
              return (
              <li key={n.id}>
                <div className="step-head">
                  <span className={`badge ${n.status}`}>{n.status}</span>
                  <span className="step-name">{title}</span>
                  <span className="step-kind">{n.kind}</span>
                </div>
                <ExpandableText
                  text={subline || prettySpanLabel(n.agent_name)}
                  className="step-agent"
                  lines={2}
                />
                {(n.attributes.decision || n.attributes.log) && (
                  <div className="step-log">{n.attributes.decision || n.attributes.log}</div>
                )}
              </li>
              );
            })}
          </ol>
        </div>
      )}

      {tab === "summary" && (
        <div className="summary-view">
          {summaryNode ? (
            <>
              {custActions.length > 0 && (
                <CustomerActions
                  actions={custActions}
                  knownAgents={new Set(byAgent.keys())}
                  onSelectSource={showAgent}
                />
              )}

              <div className="report-meta">
                {turnNumber > 0 && (
                  <span className="report-meta-item">
                    <span className="report-meta-k">Turn</span>
                    <span className="report-meta-v">
                      #{turnNumber}
                      {wokeThisTurn ? " · woke volumes" : ""}
                      {turns.length > 1 ? " (latest)" : ""}
                    </span>
                  </span>
                )}
                {producedByLabel && (
                  <span className="report-meta-item">
                    <span className="report-meta-k">Produced by</span>
                    <span className="report-meta-v" title={producedBy}>
                      {producedByLabel}
                    </span>
                  </span>
                )}
                {summaryNode.end_time && (
                  <span className="report-meta-item">
                    <span className="report-meta-k">Synthesized</span>
                    <span className="report-meta-v">
                      {new Date(summaryNode.end_time).toLocaleString()}
                    </span>
                  </span>
                )}
                <span className="report-meta-item vastdb-meta">
                  <span className="report-meta-k">VastDB</span>
                  <span className="report-meta-v">
                    {recommendationCitations.length > 0
                      ? `${recommendationCitations.length} snippet${recommendationCitations.length === 1 ? "" : "s"}`
                      : "none retrieved"}
                  </span>
                </span>
              </div>

              <div className="report-doc">
                <Markdown text={cleanSummary(summaryNode.attributes.summary)} />
              </div>

              <VastDbCitations items={recommendationCitations} />

              {recommendation && (
                <div className="reco-callout">
                  <div className="reco-label">Recommendation</div>
                  <Markdown text={recommendation} />
                  <ExpandableCitations
                    items={recommendationCitations}
                    label="supporting transcript citations"
                  />
                </div>
              )}

              {metricSections.length > 0 && (
                <div className="summary-charts">
                  <h4>Key metrics</h4>
                  <div className="chart-grid">
                    {metricSections.map((s) => (
                      <div key={s.name} className="chart-card">
                        <div className="chart-card-title">{s.name}</div>
                        <ChartSwitcher data={s.metrics} />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {highlights.length > 0 && (
                <div className="highlights">
                  <h4>Key insights (only the relevant, attributed to their sub-agent)</h4>
                  {highlights.map((h, i) => (
                    <div key={i} className="highlight">
                      <div className="highlight-main">
                        <span className="highlight-dot" />
                        <span className="highlight-text">{h.insight}</span>
                        {h.source && byAgent.has(h.source) && (
                          <button className="source-chip" onClick={() => toggle(h.source)}>
                            {expanded.includes(h.source) ? "− " : "+ "}
                            {h.source}
                          </button>
                        )}
                        {h.source && !byAgent.has(h.source) && (
                          <span className="source-chip muted-chip">{h.source}</span>
                        )}
                      </div>
                      <ExpandableCitations
                        items={citationsForSource(evByAgent, h.source)}
                        label="citations"
                      />
                    </div>
                  ))}
                </div>
              )}

              <div className="add-data">
                <span className="muted">Add more data from a sub-agent:</span>
                {subAgents.map((n) => (
                  <button
                    key={n.id}
                    className={`source-chip ${expanded.includes(n.agent_name) ? "on" : ""}`}
                    onClick={() => toggle(n.agent_name)}
                  >
                    {expanded.includes(n.agent_name) ? "− " : "+ "}
                    {n.agent_name}
                  </button>
                ))}
              </div>

              {expanded.map((name) => {
                const node = byAgent.get(name);
                return node ? (
                  <SubAgentCard key={name} node={node} onClose={() => toggle(name)} />
                ) : null;
              })}

              <div className="reports">
                <div className="reports-head">
                  <h4>Saved reports (PDF)</h4>
                  <button className="secondary-btn" onClick={saveReport} disabled={savingReport}>
                    {savingReport ? "Saving…" : "Save as PDF"}
                  </button>
                </div>
                {reports.length === 0 ? (
                  <p className="muted">
                    No reports saved yet. Save a versioned PDF snapshot — kept in VastDB by date so
                    later analyses don't overwrite earlier ones.
                  </p>
                ) : (
                  <ul className="report-list">
                    {reports.map((r) => (
                      <li key={r.report_id}>
                        <span className="report-ver">v{r.version}</span>
                        <span className="report-when">
                          {new Date(r.created_at).toLocaleString()}
                        </span>
                        <span className="report-by muted">{r.created_by}</span>
                        <span className="report-actions">
                          <button className="link-btn" onClick={() => openReport(r.report_id, false)}>
                            Open
                          </button>
                          <button className="link-btn" onClick={() => openReport(r.report_id, true)}>
                            Download
                          </button>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <details className="raw-summary">
                <summary>Full synthesized report</summary>
                <div className="markdown">
                  <Markdown text={cleanSummary(summaryNode.attributes.summary)} />
                </div>
              </details>
            </>
          ) : (
            <p className="muted">The merged summary appears once synthesis completes.</p>
          )}
        </div>
      )}

      {tab === "artifacts" && (
        <ArtifactsView
          workflowId={workflowId}
          subAgents={subAgents}
          status={workflow.status}
          storage={storage}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onOpenStorage={() => selectTab("storage")}
        />
      )}

      {tab === "storage" && (
        <StorageView
          workflowId={workflowId}
          storage={storage}
          status={workflow.status}
          onChanged={reload}
        />
      )}

      {tab === "turns" && (
        <div className="conversation">
          <p className="turns-intro muted">
            Every prompt in this shared research thread. Continue re-runs the lead with prior
            context; Graph and Summary always show the <strong>latest</strong> turn only (not a
            stacked multi-turn DAG). Save a PDF from Summary if you need an older synthesis kept.
          </p>
          {turns.length === 0 ? (
            <p className="muted">No prompts yet.</p>
          ) : (
            turns.map((turn) => {
              const isCurrent =
                (!!turn.run_id && turn.run_id === workflow.last_run_id) ||
                (!workflow.last_run_id && turn.turn_id === currentTurn?.turn_id);
              return (
              <div key={turn.turn_id} className={`turn${isCurrent ? " turn-current" : ""}`}>
                <div className="turn-head">
                  <span className="turn-seq">Prompt #{turn.seq}</span>
                  <span className="turn-user">{turn.user}</span>
                  {isCurrent && <span className="turn-chip">Showing</span>}
                  {isCurrent && wokeThisTurn && (
                    <span className="wake-chip" title="Remounted retained volumes for this continue">
                      Woke volumes
                    </span>
                  )}
                  {turn.created_at && (
                    <span className="turn-when">{new Date(turn.created_at).toLocaleString()}</span>
                  )}
                  <span className={`badge ${turn.status}`}>{turn.status}</span>
                </div>
                <div className="turn-prompt">{turn.prompt_text}</div>
              </div>
              );
            })
          )}
        </div>
      )}

      {tab === "qa" && (
        <div className="qa-view">
          <div className="qa-intro">
            <h3>Quick Q&amp;A</h3>
            <p className="muted">
              Ask a follow-up about this report — grounded in the report, its sub-agent
              findings and the corpus. No new research, no approval, no sandboxes. The
              conversation is saved with this workflow (VastDB) and doesn&apos;t change the
              research graph or report. To run new research instead, use the follow-up prompt
              on the Summary tab.
            </p>
          </div>
          {qaMessages.length === 0 && (
            <p className="qa-empty muted">
              No questions yet. Ask something specific about a statement or finding in the
              report below.
            </p>
          )}
          <QaThread messages={qaMessages} />
          <form className="prompt-bar" onSubmit={askQuestion}>
            <input
              value={qaPrompt}
              placeholder="Ask a question about this report…"
              onChange={(e) => setQaPrompt(e.target.value)}
              disabled={!summaryNode}
            />
            <button type="submit" className="btn btn-primary" disabled={qaBusy || !summaryNode}>
              <SendIcon size={15} /> {qaBusy ? "Asking…" : "Ask"}
            </button>
          </form>
          {!summaryNode && (
            <p className="muted">Quick Q&amp;A opens once the report has been synthesized.</p>
          )}
        </div>
      )}

      {/* Continuing the research is only meaningful from the reasoning views — the
          Decisions (plan/rationale) and Summary (report) tabs — so the follow-up prompt
          appears there only, not on Graph / Timeline / Artifacts / History. */}
      {(tab === "decisions" || tab === "summary") && (
        <>
          {willWake && (
            <div className="wake-banner" role="status">
              <strong>Wake on continue</strong>
              <span>
                {wake_hint?.message ||
                  "Sandbox pods are gone. Continuing remounts retained VAST block volumes (same head PVC)."}
              </span>
              {(wake_hint?.retained_pvc_count ?? 0) > 0 || (wake_hint?.snapshot_count ?? 0) > 0 ? (
                <code title="Retained volumes / VolumeSnapshots">
                  {wake_hint?.retained_pvc_count ?? 0} PVC
                  {(wake_hint?.retained_pvc_count ?? 0) === 1 ? "" : "s"}
                  {" · "}
                  {wake_hint?.snapshot_count ?? 0} snap
                  {(wake_hint?.snapshot_count ?? 0) === 1 ? "" : "s"}
                  {wake_hint?.head_pvc_present === false ? " · head missing" : ""}
                </code>
              ) : null}
              {wake_hint?.last_checkpoint ? (
                <code title="Latest VolumeSnapshot">snap: {wake_hint.last_checkpoint}</code>
              ) : null}
            </div>
          )}
          <form className="prompt-bar" onSubmit={continueWorkflow}>
            <input
              value={prompt}
              placeholder={
                willWake
                  ? "Continue — wake retained disks / last checkpoint…"
                  : "Continue this research — add a follow-up prompt…"
              }
              onChange={(e) => setPrompt(e.target.value)}
            />
            <button type="submit" className="btn btn-primary" disabled={busy}>
              <SendIcon size={15} />{" "}
              {busy ? (willWake ? "Waking…" : "Sending…") : willWake ? "Wake & continue" : "Send"}
            </button>
          </form>
        </>
      )}
    </div>
  );
}

function QaThread({
  messages,
}: {
  messages: QaMessage[];
}) {
  const threadRef = useRef<HTMLDivElement>(null);
  const last = messages[messages.length - 1];
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, last?.status, last?.answer]);
  if (messages.length === 0) return null;
  return (
    <div className="qa-thread" ref={threadRef}>
      <div className="qa-thread-head">
        <span className="qa-thread-title">Conversation</span>
        <span className="muted">Answers grounded in this report — no new research.</span>
      </div>
      {messages.map((m) => {
        const citations = jsonArr<Evidence>(m.citations_json) ?? [];
        const running = m.status === "running";
        return (
          <div key={m.run_id} className="qa-exchange">
            <div className="qa-bubble qa-q">
              <div className="qa-role">{m.user || "You"}</div>
              <div className="qa-text">{m.question}</div>
            </div>
            <div className={`qa-bubble qa-a${m.degraded ? " qa-degraded" : ""}`}>
              <div className="qa-role">
                {running
                  ? "Assistant"
                  : m.produced_by
                    ? shortModelName(m.produced_by)
                    : "Assistant"}
              </div>
              {running ? (
                <QaThinking />
              ) : (
                <>
                  <div className="qa-text">
                    <Markdown text={m.answer} />
                  </div>
                  {citations.length > 0 && (
                    <ExpandableCitations
                      items={citations}
                      label="supporting transcript citations"
                    />
                  )}
                </>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const QA_THINKING_NOTES = [
  "Reading the report…",
  "Searching the corpus…",
  "Grounding citations…",
  "Drafting the answer…",
];

function QaThinking() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(
      () => setI((n) => (n + 1) % QA_THINKING_NOTES.length),
      2200,
    );
    return () => clearInterval(id);
  }, []);
  return (
    <div className="qa-text qa-thinking" aria-live="polite">
      <span className="qa-thinking-note">{QA_THINKING_NOTES[i]}</span>
      <span className="qa-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}

