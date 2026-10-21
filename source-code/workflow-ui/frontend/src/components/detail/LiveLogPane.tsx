import { useEffect, useMemo, useRef } from "react";
import { displayFor } from "../../layout";
import { isActive, statusColor } from "../../theme";
import type { GraphNode } from "../../types";
import { TerminalIcon } from "../icons";

interface LogLine {
  key: string;
  nodeId: string;
  ts: number;
  time: string;
  agent: string;
  status: string;
  kind: string;
  message: string;
}

function fmtTime(ms: number): string {
  if (!Number.isFinite(ms)) return "--:--:--";
  const d = new Date(ms);
  return d.toLocaleTimeString([], { hour12: false });
}

export function LiveLogPane({
  nodes,
  status,
  selectedId,
  onSelect,
}: {
  nodes: GraphNode[];
  status: string;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  const active = isActive(status);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);

  const lines = useMemo<LogLine[]>(() => {
    const out: LogLine[] = [];
    for (const n of nodes) {
      const start = Date.parse(n.start_time);
      const end = Date.parse(n.end_time);
      const log = n.attributes.log?.trim();
      const decision = n.attributes.decision?.trim();
      const { title } = displayFor(n);
      if (log) {
        out.push({
          key: `${n.id}:log`,
          nodeId: n.id,
          ts: Number.isFinite(start) ? start : 0,
          time: fmtTime(start),
          agent: title,
          status: n.status,
          kind: n.kind,
          message: log,
        });
      }
      if (decision && decision !== log) {
        const t = Number.isFinite(end) ? end : start;
        out.push({
          key: `${n.id}:dec`,
          nodeId: n.id,
          ts: Number.isFinite(t) ? t : 0,
          time: fmtTime(t),
          agent: title,
          status: n.status,
          kind: n.kind,
          message: `▸ ${decision}`,
        });
      }
    }
    out.sort((a, b) => a.ts - b.ts || a.key.localeCompare(b.key));
    return out;
  }, [nodes]);

  function onScroll() {
    const el = bodyRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }
  useEffect(() => {
    const el = bodyRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <aside className="log-pane" aria-label="Live agent logs">
      <div className="log-pane-head">
        <span className="log-pane-title">
          <TerminalIcon size={13} /> Agent logs
        </span>
        {active ? (
          <span className="log-pane-live">
            <span className="log-live-dot" /> live
          </span>
        ) : (
          <span className="log-pane-count">{lines.length}</span>
        )}
      </div>
      <div className="log-pane-body" ref={bodyRef} onScroll={onScroll}>
        {lines.length === 0 ? (
          <div className="log-pane-empty">
            {active ? "Waiting for the first agent to report…" : "No agent logs for this run."}
          </div>
        ) : (
          lines.map((l) => (
            <div
              key={l.key}
              className={`log-line${l.nodeId === selectedId ? " sel" : ""}${onSelect ? " clickable" : ""}`}
              onClick={onSelect ? () => onSelect(l.nodeId) : undefined}
              title={onSelect ? "Select this step in the graph" : undefined}
            >
              <span className="log-time">{l.time}</span>
              <span
                className="log-agent"
                style={{ color: statusColor(l.status) }}
                title={`${l.agent} · ${l.status}`}
              >
                {l.agent}
              </span>
              <span className="log-msg">{l.message}</span>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
