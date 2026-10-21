import {
  confidence,
  considerations,
  jsonArr,
  keyFindings,
  metrics,
  sources,
  type PlanItem,
} from "../../attrs";
import type { GraphNode } from "../../types";
import { ChartSwitcher, ConfidenceMeter } from "../charts";
import { Markdown } from "../Markdown";

export function SubAgentCard({ node, onClose }: { node: GraphNode; onClose: () => void }) {
  const m = metrics(node);
  const kf = keyFindings(node);
  const conf = confidence(node);
  return (
    <div className="subagent-card">
      <div className="subagent-head">
        <span className="subagent-name">{node.agent_name}</span>
        {conf !== null && <ConfidenceMeter value={conf} />}
        <button className="chip" onClick={onClose}>
          close
        </button>
      </div>
      {kf.length > 0 && (
        <ul className="findings">
          {kf.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
      {m.length > 0 && <ChartSwitcher data={m} />}
      {node.attributes.data_produced && (
        <div className="data-block markdown">
          <Markdown text={node.attributes.data_produced} />
        </div>
      )}
    </div>
  );
}

export function DecisionsView({
  planNode,
  subAgents,
}: {
  planNode: GraphNode | null;
  subAgents: GraphNode[];
}) {
  const plan = jsonArr<PlanItem>(planNode?.attributes.plan_json);
  if (!planNode && subAgents.length === 0) {
    return (
      <p className="muted">Decision rationale appears once the research-lead plans the run.</p>
    );
  }
  return (
    <div className="decisions">
      {plan.length > 0 && (
        <section>
          <h4>How the research-lead decomposed the task</h4>
          <div className="decision-cards">
            {plan.map((p, i) => (
              <div key={i} className="decision-card">
                <div className="decision-name">{p.name}</div>
                <div className="decision-focus">{p.focus}</div>
                {p.rationale && <div className="decision-why">Why: {p.rationale}</div>}
              </div>
            ))}
          </div>
        </section>
      )}
      <section>
        <h4>What each sub-agent considered</h4>
        <div className="decision-cards">
          {subAgents.map((n) => {
            const cons = considerations(n);
            const conf = confidence(n);
            const srcs = sources(n);
            return (
              <div key={n.id} className="decision-card">
                <div className="decision-name">{n.agent_name}</div>
                {conf !== null && (
                  <div className="decision-conf">
                    <span className="muted">confidence</span>
                    <ConfidenceMeter value={conf} />
                  </div>
                )}
                {cons.length > 0 ? (
                  <ul className="considerations">
                    {cons.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                ) : (
                  <div className="muted">No explicit considerations recorded.</div>
                )}
                {srcs.length > 0 && (
                  <div className="chips">
                    {srcs.map((s, i) => (
                      <span key={i} className="chip">
                        {s}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
