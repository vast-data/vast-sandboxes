import { useState } from "react";
import type { CitedEvidence, Evidence } from "../../types";
import { EvidenceList } from "./EvidenceList";

export function ExpandableCitations({
  items,
  label = "citations",
}: {
  items: Array<Evidence | CitedEvidence>;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  if (items.length === 0) return null;
  return (
    <div className="cite-wrap">
      <button
        type="button"
        className="cite-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`cite-caret ${open ? "open" : ""}`} aria-hidden="true">
          ▸
        </span>
        {open ? "Hide" : "Show"} {label} ({items.length})
      </button>
      {open && <EvidenceList items={items} />}
    </div>
  );
}
