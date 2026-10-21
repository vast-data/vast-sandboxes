import { useEffect, useMemo, useState } from "react";
import { approvePlan, type PlanItemInput } from "../../api";
import { jsonArr, type PlanItem } from "../../attrs";
import { useToast } from "../../toast";
import type { GraphNode, WorkflowDetail } from "../../types";
import { FlowIcon, PlusIcon, TrashIcon } from "../icons";

export function PlanApproval({
  planNode,
  workflowId,
  onApproved,
}: {
  planNode: GraphNode | null;
  workflowId: string;
  onApproved: (detail: WorkflowDetail) => void;
}) {
  const toast = useToast();
  const proposed = useMemo<PlanItem[]>(
    () => jsonArr<PlanItem>(planNode?.attributes.plan_json),
    [planNode],
  );
  const [items, setItems] = useState<PlanItemInput[]>(() =>
    proposed.map((p) => ({ name: p.name, focus: p.focus, rationale: p.rationale ?? "" })),
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (proposed.length === 0) return;
    setItems((prev) =>
      prev.length === 0
        ? proposed.map((p) => ({ name: p.name, focus: p.focus, rationale: p.rationale ?? "" }))
        : prev,
    );
  }, [proposed]);

  function update(i: number, patch: Partial<PlanItemInput>) {
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));
  }
  function remove(i: number) {
    setItems((prev) => prev.filter((_, idx) => idx !== i));
  }
  function add() {
    setItems((prev) => [...prev, { name: "new-agent", focus: "", rationale: "" }]);
  }

  const valid = items.filter((it) => it.name.trim() && it.focus.trim());

  async function approve() {
    if (valid.length === 0) {
      toast.push("Add at least one sub-agent with a name and focus.", "error");
      return;
    }
    setBusy(true);
    try {
      const detail = await approvePlan(workflowId, valid);
      toast.push(`Plan approved — spawning ${valid.length} OpenShell sandbox${valid.length === 1 ? "" : "es"}`, "success");
      onApproved(detail);
    } catch {
      toast.push("Could not approve the plan.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="plan-approval">
      <div className="pa-head">
        <div className="pa-title">
          <span className="pa-dot" /> Plan awaiting your approval
        </div>
        <div className="pa-sub">
          The research-lead proposed {items.length} sub-agent{items.length === 1 ? "" : "s"}. Review
          and edit below — no sandboxes are created until you approve.
        </div>
      </div>

      <div className="pa-items">
        {items.map((it, i) => (
          <div key={i} className="pa-item">
            <div className="pa-item-row">
              <input
                className="pa-name"
                value={it.name}
                spellCheck={false}
                placeholder="kebab-case-name"
                onChange={(e) => update(i, { name: e.target.value })}
              />
              <button
                className="pa-remove"
                title="Remove this sub-agent"
                onClick={() => remove(i)}
              >
                <TrashIcon size={14} />
              </button>
            </div>
            <textarea
              className="pa-focus"
              value={it.focus}
              rows={5}
              placeholder="What should this sub-agent investigate?"
              onChange={(e) => update(i, { focus: e.target.value })}
            />
            {it.rationale && <div className="pa-why">Why: {it.rationale}</div>}
          </div>
        ))}
      </div>

      <div className="pa-actions">
        <button className="btn btn-ghost" onClick={add} disabled={busy}>
          <PlusIcon size={14} /> Add sub-agent
        </button>
        <button className="btn btn-primary" onClick={approve} disabled={busy}>
          <FlowIcon size={15} /> {busy ? "Approving…" : `Approve & run (${valid.length})`}
        </button>
      </div>
    </div>
  );
}
