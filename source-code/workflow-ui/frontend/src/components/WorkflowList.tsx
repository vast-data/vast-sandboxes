import type { User, WorkflowSummary } from "../types";
import { TrashIcon } from "./icons";

interface Props {
  workflows: WorkflowSummary[];
  selected: string | null;
  onSelect: (workflowId: string) => void;
  emptyLabel?: string;
  user?: User | null;
  onDelete?: (workflow: WorkflowSummary) => void;
  deletingId?: string | null;
}

export function WorkflowList({
  workflows,
  selected,
  onSelect,
  emptyLabel,
  user,
  onDelete,
  deletingId,
}: Props) {
  if (workflows.length === 0) {
    return (
      <ul className="workflow-list">
        <p className="muted" style={{ padding: "0 4px" }}>
          {emptyLabel ?? "No workflows yet. Start a new research."}
        </p>
      </ul>
    );
  }
  const canDelete = (wf: WorkflowSummary) =>
    !!onDelete && !!user && (user.is_admin || wf.user === user.username);
  return (
    <ul className="workflow-list">
      {workflows.map((wf) => (
        <li
          key={wf.workflow_id}
          className={`wf-item ${wf.workflow_id === selected ? "active" : ""}`}
          onClick={() => onSelect(wf.workflow_id)}
        >
          <div className="wf-item-head">
            <div className="wf-title">{wf.root_agent}</div>
            {canDelete(wf) && (
              <button
                className="wf-del"
                aria-label={`Delete workflow ${wf.root_agent}`}
                title="Delete this workflow and everything it owns (sandboxes, snapshots, PVCs)"
                disabled={deletingId === wf.workflow_id}
                onClick={(e) => {
                  // Never let the row's select handler fire from the delete button.
                  e.stopPropagation();
                  onDelete?.(wf);
                }}
              >
                <TrashIcon size={13} />
              </button>
            )}
          </div>
          <div className="wf-meta">
            <span className={`badge ${wf.status}`}>{wf.status}</span>
            <span>{wf.department}</span>
            {wf.agent_count > 0 && (
              <span
                title={
                  wf.subagent_count > 0
                    ? "1 lead (in-platform) + OpenShell sandboxes"
                    : "Lead has not fanned out OpenShell sandboxes yet"
                }
              >
                ·{" "}
                {wf.subagent_count > 0
                  ? `1 lead + ${wf.subagent_count} OpenShell`
                  : "lead only"}
              </span>
            )}
          </div>
          <div className="wf-meta" style={{ marginTop: 4 }}>
            {wf.user}
          </div>
        </li>
      ))}
    </ul>
  );
}
