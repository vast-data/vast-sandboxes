import { useState } from "react";
import type { CitedEvidence, Evidence } from "../../types";
import { EvidenceList } from "./EvidenceList";

function unique(values: Array<string | undefined>): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of values) {
    const value = (raw || "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

/** The summary's VastDB grounding: which corpus snippets the report was written from,
 *  with a single control that opens the citation text. */
export function VastDbCitations({
  items,
}: {
  items: Array<Evidence | CitedEvidence>;
}) {
  const [open, setOpen] = useState(items.length > 0);
  const companies = unique(items.map((e) => e.customer));
  const workstreams = unique(items.map((e) => ("source" in e ? e.source : "")));
  const grounded = items.length > 0;

  return (
    <section
      className={`vastdb-panel${grounded ? " has-data" : " empty"}`}
      aria-label="VastDB citations"
    >
      <div className="vastdb-panel-head">
        <div className="vastdb-panel-copy">
          <div className="vastdb-kicker">From VastDB</div>
          <h3>
            {grounded
              ? `${items.length} transcript snippet${items.length === 1 ? "" : "s"}`
              : "No transcript snippets"}
          </h3>
          <p>
            {grounded
              ? "Retrieved from the earnings-call corpus. The summary above uses this VastDB data."
              : "This summary has no VastDB citations. No call text was retrieved for it."}
          </p>
        </div>
        <button
          type="button"
          className="secondary-btn vastdb-view"
          disabled={!grounded}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "Hide citations" : "View citations"}
        </button>
      </div>
      {grounded && (companies.length > 0 || workstreams.length > 0) && (
        <div className="vastdb-chips">
          {companies.map((name) => (
            <span key={`c-${name}`} className="vastdb-chip company">
              {name}
            </span>
          ))}
          {workstreams.map((name) => (
            <span key={`s-${name}`} className="vastdb-chip source">
              {name}
            </span>
          ))}
        </div>
      )}
      {open && grounded && <EvidenceList items={items} />}
    </section>
  );
}
