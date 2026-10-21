import { memo } from "react";
import { Handle, type NodeProps, Position } from "reactflow";
import type { TurboData, TurboIcon, TurboRole } from "../../layout";
import { CubeIcon, DatabaseIcon, FileIcon, FlowIcon, SnapshotIcon } from "../icons";

const ICONS: Record<TurboIcon, typeof CubeIcon> = {
  lead: FlowIcon,
  agent: CubeIcon,
  retrieval: DatabaseIcon,
  summary: FileIcon,
  storage: SnapshotIcon,
};

// Maps a raw span status to the stable class the CSS keys its glow off of.
function tone(status: string): string {
  if (status === "error") return "error";
  if (status === "running" || status === "started") return "running";
  if (status === "ok" || status === "completed") return "ok";
  return "idle";
}

function Badge({ status }: { status: string }) {
  const t = tone(status);
  if (t === "running") return <span className="tn-badge spin" aria-label="running" />;
  if (t === "error")
    return (
      <span className="tn-badge err" aria-label="error">
        ✕
      </span>
    );
  if (t === "ok")
    return (
      <span className="tn-badge ok" aria-label="completed">
        ✓
      </span>
    );
  return <span className="tn-badge idle" aria-label="pending" />;
}

function RoleTag({ role }: { role: TurboRole }) {
  if (!role) return null;
  const lead = role === "lead";
  return (
    <span
      className={`tn-role ${lead ? "lead" : "openshell"}`}
      title={
        lead
          ? "Lead — orchestrates in the backend (no sandbox pod)"
          : "OpenShell — NemoClaw / OpenClaw agent in a governed sandbox on VAST block"
      }
    >
      {lead ? "Lead" : "OpenShell"}
    </span>
  );
}

function TurboNodeInner({ data }: NodeProps<TurboData>) {
  const Icon = ICONS[data.icon] ?? CubeIcon;
  return (
    <div className={`turbo-node tn-${tone(data.status)} ${data.selected ? "tn-selected" : ""}`}>
      <div className="turbo-glow" />
      <div className="turbo-body">
        <div className="turbo-icon">
          <Icon size={18} />
        </div>
        <div className="turbo-text">
          <span className="turbo-title" title={data.fullTitle}>
            {data.title}
            <RoleTag role={data.role} />
          </span>
          {data.subline && (
            <span className="turbo-sub" title={data.fullSubline}>
              {data.subline}
            </span>
          )}
        </div>
        <Badge status={data.status} />
      </div>
      <Handle type="target" position={Position.Left} className="turbo-handle" />
      <Handle type="source" position={Position.Right} className="turbo-handle" />
    </div>
  );
}

export const TurboNode = memo(TurboNodeInner);
