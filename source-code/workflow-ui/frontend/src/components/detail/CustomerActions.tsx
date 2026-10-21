import { useMemo } from "react";
import type { CustomerAction } from "../../attrs";

const STAGE_ORDER = [
  "conglomerate",
  "financial",
  "healthcare",
  "consumer goods",
  "industrial goods",
  "basic materials",
  "technology",
  "utilities",
  "services",
  "discovery",
  "qualification",
  "technical validation",
  "proof of concept",
  "poc",
  "evaluation",
  "proposal",
  "negotiation",
  "procurement",
  "closed won",
  "closed lost",
];

function stageRank(stage: string): number {
  const s = stage.trim().toLowerCase();
  const i = STAGE_ORDER.findIndex((k) => s.includes(k));
  return i === -1 ? STAGE_ORDER.length : i;
}

export function CustomerActions({
  actions,
  knownAgents,
  onSelectSource,
}: {
  actions: CustomerAction[];
  knownAgents: Set<string>;
  onSelectSource: (name: string) => void;
}) {
  const columns = useMemo(() => {
    const groups = new Map<string, CustomerAction[]>();
    for (const a of actions) {
      const key = a.stage.trim() || "Unspecified sector";
      const arr = groups.get(key);
      if (arr) arr.push(a);
      else groups.set(key, [a]);
    }
    return [...groups.entries()].sort((x, y) => stageRank(x[0]) - stageRank(y[0]));
  }, [actions]);

  if (actions.length === 0) return null;

  return (
    <div className="cust-actions">
      <h4>Per-company next steps</h4>
      <div className="ca-board">
        {columns.map(([stage, cards]) => (
          <div key={stage} className="ca-col">
            <div className="ca-col-head">
              <span className="ca-stage">{stage}</span>
              <span className="ca-count">{cards.length}</span>
            </div>
            <div className="ca-col-body">
              {cards.map((c, i) => (
                <div key={`${c.customer}-${i}`} className="ca-card">
                  <div className="ca-customer">{c.customer}</div>
                  <div className="ca-action">{c.action}</div>
                  {c.source &&
                    (knownAgents.has(c.source) ? (
                      <button
                        className="ca-source"
                        title="Show the sub-agent this came from"
                        onClick={() => onSelectSource(c.source)}
                      >
                        {c.source}
                      </button>
                    ) : (
                      <span className="ca-source muted-chip">{c.source}</span>
                    ))}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
