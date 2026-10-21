import { useEffect, useMemo, useState } from "react";
import { getWorkflowDetail } from "../api";
import {
  cleanSummary,
  confidence,
  type Highlight,
  isSubAgent,
  jsonArr,
  runCost,
} from "../attrs";
import type { WorkflowDetail, WorkflowSummary } from "../types";

interface Props {
  workflows: WorkflowSummary[];
}

interface Loaded {
  detail: WorkflowDetail | null;
  loading: boolean;
}

function useDetail(id: string | ""): Loaded {
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!id) {
      setDetail(null);
      return;
    }
    let active = true;
    setLoading(true);
    getWorkflowDetail(id)
      .then((d) => {
        if (active) setDetail(d);
      })
      .catch(() => {
        if (active) setDetail(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id]);
  return { detail, loading };
}

function avgConfidence(detail: WorkflowDetail | null): number | null {
  if (!detail) return null;
  const subs = detail.graph.nodes.filter(isSubAgent);
  const vals = subs.map(confidence).filter((v): v is number => v !== null);
  if (vals.length === 0) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function CompareColumn({ detail, loading }: Loaded) {
  const [showAllInsights, setShowAllInsights] = useState(false);
  const cost = useMemo(() => runCost(detail?.graph.nodes ?? []), [detail]);
  if (loading) return <div className="compare-col muted">Loading…</div>;
  if (!detail) return <div className="compare-col muted">Pick a workflow to compare.</div>;

  const { workflow, graph } = detail;
  const subAgents = graph.nodes.filter(isSubAgent);
  const summaryNode = graph.nodes.find((n) => n.attributes.summary);
  const highlights = jsonArr<Highlight>(summaryNode?.attributes.highlights_json);
  const conf = avgConfidence(detail);

  const rows: [string, string][] = [
    ["Status", workflow.status],
    ["Department", workflow.department],
    ["Sub-agents", String(subAgents.length)],
    ["Total tokens", cost.totalTokens.toLocaleString()],
    ["Tool calls", String(cost.toolCalls)],
    ["Snippets retrieved", String(cost.retrievedSnippets)],
    ["Est. LLM cost", `$${cost.estUsd.toFixed(4)}`],
    ["Avg confidence", conf === null ? "—" : `${Math.round(conf * 100)}%`],
  ];

  return (
    <div className="compare-col">
      <div className="compare-col-head">
        <h3>{workflow.title}</h3>
        <span className={`badge ${workflow.status}`}>{workflow.status}</span>
      </div>
      <table className="compare-table">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <th>{k}</th>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="compare-insights">
        <h4>Key insights</h4>
        {highlights.length === 0 ? (
          <p className="muted">No synthesized insights yet.</p>
        ) : (
          <ul>
            {(showAllInsights ? highlights : highlights.slice(0, 6)).map((h, i) => (
              <li key={i}>{h.insight}</li>
            ))}
          </ul>
        )}
        {highlights.length > 6 && (
          <button
            type="button"
            className="expandable-text-toggle"
            aria-expanded={showAllInsights}
            onClick={() => setShowAllInsights((value) => !value)}
          >
            {showAllInsights ? "Show fewer insights" : `Show all ${highlights.length} insights`}
          </button>
        )}
      </div>
      {summaryNode && (
        <details className="compare-summary">
          <summary>Summary</summary>
          <p>{cleanSummary(summaryNode.attributes.summary)}</p>
        </details>
      )}
    </div>
  );
}

export function CompareView({ workflows }: Props) {
  const [left, setLeft] = useState<string>("");
  const [right, setRight] = useState<string>("");
  const leftData = useDetail(left);
  const rightData = useDetail(right);

  const options = workflows;

  return (
    <div className="compare-view">
      <div className="compare-head">
        <h2>Compare runs</h2>
        <p className="muted">Put two workflows side by side — cost, coverage, confidence and insights.</p>
      </div>
      <div className="compare-pickers">
        <select value={left} onChange={(e) => setLeft(e.target.value)} aria-label="Left workflow">
          <option value="">Select workflow A…</option>
          {options.map((w) => (
            <option key={w.workflow_id} value={w.workflow_id}>
              {w.root_agent} · {w.department} ({w.status})
            </option>
          ))}
        </select>
        <span className="compare-vs">vs</span>
        <select value={right} onChange={(e) => setRight(e.target.value)} aria-label="Right workflow">
          <option value="">Select workflow B…</option>
          {options.map((w) => (
            <option key={w.workflow_id} value={w.workflow_id}>
              {w.root_agent} · {w.department} ({w.status})
            </option>
          ))}
        </select>
      </div>
      <div className="compare-grid">
        <CompareColumn detail={leftData.detail} loading={leftData.loading} />
        <CompareColumn detail={rightData.detail} loading={rightData.loading} />
      </div>
    </div>
  );
}
