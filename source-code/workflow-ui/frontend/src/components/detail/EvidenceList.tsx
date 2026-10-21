import type { CitedEvidence, Evidence } from "../../types";

export function EvidenceList({
  items,
}: {
  items: Array<Evidence | CitedEvidence>;
}) {
  if (items.length === 0) {
    return <p className="muted cite-empty">No transcript citations recorded for this section.</p>;
  }
  return (
    <div className="evidence-list">
      {items.map((e, i) => {
        const key = `${e.engagement_id || e.customer}-${i}`;
        return (
          <div key={key} className="evidence-card">
            <div className="evidence-head">
              <span className="evidence-eng">{e.customer || e.engagement_id || "?"}</span>
              {e.sales_stage && <span className="ev-stage">{e.sales_stage}</span>}
              {e.speaker_type && (
                <span className={`ev-speaker ${e.speaker_type}`}>{e.speaker_type}</span>
              )}
              {e.call_date && <span className="muted">{e.call_date}</span>}
              {e.table && <span className="ev-table">{e.table}</span>}
              {"source" in e && e.source && (
                <span className="cite-agent">{e.source}</span>
              )}
              {e.distance != null && (
                <span className="evidence-dist" title="cosine distance (lower = closer)">
                  d={typeof e.distance === "number" ? e.distance.toFixed(3) : e.distance}
                </span>
              )}
            </div>
            {e.opportunity && <div className="evidence-opp">{e.opportunity}</div>}
            <div className="evidence-text">{e.text}</div>
          </div>
        );
      })}
    </div>
  );
}
