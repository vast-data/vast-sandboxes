import { useEffect, useState } from "react";
import type { RunCost } from "../../attrs";
import { isActive } from "../../theme";
import { displayFor } from "../../layout";
import type { GraphNode, ToolInfo } from "../../types";

export function CapabilitiesBar({
  tools,
  fallbackNames = [],
}: {
  tools: ToolInfo[];
  fallbackNames?: string[];
}) {
  const live = tools.length > 0;
  const visible = live
    ? tools
    : fallbackNames.map((name) => ({
        name,
        description: "department MCP tool",
      }));
  const configuredOnly = !live && visible.length > 0;
  return (
    <div className="capabilities">
      <div className="cap-label">
        <span className={`cap-dot${configuredOnly ? " cap-dot-configured" : ""}`} />
        MCP tools · {visible.length}
        {configuredOnly ? (
          <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>
            configured
          </span>
        ) : null}
      </div>
      {visible.length === 0 ? (
        <span className="muted" style={{ fontSize: 12 }}>
          No MCP tools configured for this department.
        </span>
      ) : (
        <div className="cap-chips">
          {visible.map((t) => (
            <span key={t.name} className="cap-chip" title={t.description}>
              {t.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function CostStrip({ cost }: { cost: RunCost }) {
  const stats: [string, string][] = [
    ["Total tokens", cost.totalTokens.toLocaleString()],
    ["Prompt", cost.promptTokens.toLocaleString()],
    ["Completion", cost.completionTokens.toLocaleString()],
    ["Embedding", cost.embedTokens.toLocaleString()],
    ["Tool calls", String(cost.toolCalls)],
    ["Snippets retrieved", String(cost.retrievedSnippets)],
    ["Est. LLM cost", `$${cost.estUsd.toFixed(4)}`],
  ];
  return (
    <div className="cost-strip">
      {stats.map(([label, value]) => (
        <div key={label} className="cost-cell">
          <span className="cost-value">{value}</span>
          <span className="cost-label">{label}</span>
        </div>
      ))}
    </div>
  );
}

function fmtElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function LiveStatus({
  nodes,
  status,
  cost,
}: {
  nodes: GraphNode[];
  status: string;
  cost: RunCost;
}) {
  const active = isActive(status);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);

  if (!active) return null;

  const running = nodes.filter((n) => n.status === "running" || n.status === "started");
  const startMs = nodes.reduce((min, n) => {
    const t = Date.parse(n.start_time);
    return Number.isFinite(t) && t < min ? t : min;
  }, Number.POSITIVE_INFINITY);
  const elapsed = Number.isFinite(startMs) ? Math.max(0, Math.floor((now - startMs) / 1000)) : null;

  return (
    <div className="live-status" role="status" aria-live="polite">
      <span className="live-pulse" aria-hidden="true" />
      <span className="live-title">Agents working</span>
      {elapsed !== null && <span className="live-elapsed">{fmtElapsed(elapsed)}</span>}
      <span className="live-metric">{cost.totalTokens.toLocaleString()} tok</span>
      <span className="live-metric">${cost.estUsd.toFixed(4)}</span>
      <div className="live-agents">
        {running.length === 0 ? (
          <span className="muted">orchestrating…</span>
        ) : (
          running.map((n) => {
            const { title } = displayFor(n);
            return (
            <span key={n.id} className="live-agent" title={title}>
              {title}
            </span>
            );
          })
        )}
      </div>
    </div>
  );
}
