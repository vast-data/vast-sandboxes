import {
  confidence,
  considerations,
  endpointLabel,
  evidence,
  isRetrieval,
  keyFindings,
  metrics,
  shortModelName,
  sources,
} from "../../attrs";
import type { GraphNode } from "../../types";
import { ChartSwitcher, ConfidenceMeter } from "../charts";
import { EvidenceList } from "./EvidenceList";

// Sub-agent spans record the engine as tool="openclaw:<model>" (agent_runner.py).
const OPENCLAW_TOOL_PREFIX = "openclaw:";

function EvidencePanel({ node }: { node: GraphNode }) {
  const items = evidence(node);
  const status = node.attributes.retrieval_status ?? "";
  const topK = node.attributes.top_k ?? "";
  const embedModel = node.attributes.embed_model ?? "";
  const embedEndpoint = node.attributes.embed_endpoint ?? "";
  let appliedFilters: [string, string][] = [];
  try {
    const parsed = JSON.parse(node.attributes.filters_json ?? "{}");
    if (parsed && typeof parsed === "object") {
      appliedFilters = Object.entries(parsed).map(([k, v]) => [k, String(v)]);
    }
  } catch {
    appliedFilters = [];
  }
  const embedNote = embedModel ? (
    <div className="muted" style={{ fontSize: 11 }} title={embedEndpoint}>
      query embedded with {shortModelName(embedModel)}
      {embedEndpoint ? ` @ ${endpointLabel(embedEndpoint)}` : ""}
    </div>
  ) : null;

  if (items.length === 0) {
    return (
      <div className="kv">
        <span className="k">Evidence</span>
        <div className="muted">No snippets retrieved (status: {status || "n/a"}).</div>
        {embedNote}
      </div>
    );
  }
  return (
    <div className="kv">
      <span className="k">
        Evidence · {items.length} snippet{items.length === 1 ? "" : "s"} (top_k {topK})
      </span>
      {appliedFilters.length > 0 && (
        <div className="chips" style={{ marginBottom: 6 }}>
          <span className="muted" style={{ fontSize: 11 }}>
            metadata filter:
          </span>
          {appliedFilters.map(([k, v]) => (
            <span key={k} className="cap-chip">
              {k}={v}
            </span>
          ))}
        </div>
      )}
      {embedNote}
      <EvidenceList items={items} />
    </div>
  );
}

export function NodeInspector({ node }: { node: GraphNode | null }) {
  if (!node) {
    return (
      <aside className="inspector">
        <h3>Node inspector</h3>
        <p className="muted">
          Click a node in the DAG to see its inputs, decisions, charts, considerations and data.
        </p>
      </aside>
    );
  }
  const a = node.attributes;
  const m = metrics(node);
  const kf = keyFindings(node);
  const cons = considerations(node);
  const srcs = sources(node);
  const conf = confidence(node);
  const tokens =
    a.tokens_prompt || a.tokens_completion
      ? `${Number(a.tokens_prompt ?? 0) + Number(a.tokens_completion ?? 0)}`
      : null;
  return (
    <aside className="inspector">
      <h3>{node.label}</h3>
      <div className="kv">
        <span className="k">Agent</span>
        <span className="v">{node.agent_name}</span>
      </div>
      <div className="wf-meta" style={{ marginBottom: 12 }}>
        <span className={`badge ${node.status}`}>{node.status}</span>
        <span>{node.kind}</span>
        {a.phase && <span>· {a.phase}</span>}
      </div>
      {conf !== null && (
        <div className="kv">
          <span className="k">Confidence</span>
          <ConfidenceMeter value={conf} />
        </div>
      )}
      {a.engine && (
        <div className="kv">
          <span className="k">Engine</span>
          <span className="v" title={a.engine_endpoint}>
            {a.engine}
            {a.tool?.startsWith(OPENCLAW_TOOL_PREFIX) &&
              ` · ${shortModelName(a.tool.slice(OPENCLAW_TOOL_PREFIX.length))}`}
            {a.engine_endpoint && (
              <span className="muted"> @ {endpointLabel(a.engine_endpoint)}</span>
            )}
          </span>
        </div>
      )}
      {a.duration_ms && (
        <div className="kv">
          <span className="k">Duration</span>
          <span className="v">{a.duration_ms} ms</span>
        </div>
      )}
      {tokens && (
        <div className="kv">
          <span className="k">Tokens</span>
          <span className="v">{tokens}</span>
        </div>
      )}
      {a.input && (
        <div className="kv">
          <span className="k">{isRetrieval(node) ? "Query (embedded)" : "Input"}</span>
          <div className="data-block">{a.input}</div>
        </div>
      )}
      {isRetrieval(node) && <EvidencePanel node={node} />}
      {kf.length > 0 && (
        <div className="kv">
          <span className="k">Key findings</span>
          <ul className="findings">
            {kf.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      )}
      {m.length > 0 && (
        <div className="kv">
          <span className="k">Metrics</span>
          <ChartSwitcher data={m} />
        </div>
      )}
      {cons.length > 0 && (
        <div className="kv">
          <span className="k">Considerations</span>
          <ul className="considerations">
            {cons.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {srcs.length > 0 && (
        <div className="kv">
          <span className="k">Data relied on</span>
          <div className="chips">
            {srcs.map((s, i) => (
              <span key={i} className="chip">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}
      {a.decision && (
        <div className="kv">
          <span className="k">Decision</span>
          <div className="data-block">{a.decision}</div>
        </div>
      )}
      {a.data_produced && (
        <div className="kv">
          <span className="k">Data produced</span>
          <div className="data-block">{a.data_produced}</div>
        </div>
      )}
      {a.log && (
        <div className="kv">
          <span className="k">Log</span>
          <div className="log-viewer">{a.log}</div>
        </div>
      )}
    </aside>
  );
}
