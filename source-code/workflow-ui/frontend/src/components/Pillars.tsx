import type { ReactNode } from "react";
import {
  ColumnsIcon,
  CubeIcon,
  DatabaseIcon,
  EyeIcon,
  FlowIcon,
  ShieldIcon,
  TerminalIcon,
} from "./icons";

const VAST_KEYWORDS =
  /(\bzero-copy\b|\bNVMe\/TCP\b|\bFile CSI\b|\bBlock CSI\b|\bVastDB\b|\bVMS\b|\bVAST\b|\bCSI\b|\bBlock\b|\bFile\b|\bRWO\b|\bS3\b|\bprotocol\b|\bsnapshot\b|\bclone\b|\bvolume\b|\bNemoClaw\b|\bOpenShell\b|\bOpenClaw\b)/gi;

function highlightVast(text: string): ReactNode[] {
  return text.split(VAST_KEYWORDS).map((part, i) =>
    i % 2 === 1 ? (
      <span key={i} className="vast-kw">
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

const PILLARS = [
  {
    Icon: FlowIcon,
    title: "NemoClaw + OpenShell",
    body: "Every sub-agent is a real NVIDIA OpenClaw agent under a NemoClaw blueprint, created and policed by the OpenShell gateway — not a bare pod and not a one-shot prompt.",
  },
  {
    Icon: CubeIcon,
    title: "VAST block sandboxes",
    body: "OpenShell mounts each agent's own NVMe/TCP Block volume (RWO), forked zero-copy from the workflow head at fan-out. VolumeSnapshot checkpoints before spawn and again when each sub-agent finishes; roll back corrupted sandboxes or wake on remote block after a pod dies.",
  },
  {
    Icon: DatabaseIcon,
    title: "Grounded in VastDB",
    body: "Agents call MCP tools themselves to pull real evidence from VastDB vector search — every claim is traceable to the source call, and you can plug in your own tools.",
  },
  {
    Icon: TerminalIcon,
    title: "Live OpenShell shell",
    body: "Attach an interactive terminal straight into a running OpenShell sandbox to watch — or drive — an agent while it works.",
  },
  {
    Icon: EyeIcon,
    title: "Fully observable & self-healing",
    body: "Live DAG, streaming per-agent logs, token spend and cost — the whole run unfolds in front of you; transient failures retry with backoff and anything degraded is shown honestly, never faked.",
  },
  {
    Icon: ColumnsIcon,
    title: "Model-flexible",
    body: "Swap the NVIDIA NIM model per deploy — Llama 3.1 8B through 405B, Nemotron and more — with zero code changes.",
  },
  {
    Icon: ShieldIcon,
    title: "VAST-native identity & isolation",
    body: "Users sign in with their own VAST identity via VMS, and every request is scoped to their department — its own namespace, VastDB data and S3 bucket — enforced from the API down to the volume.",
  },
  {
    Icon: CubeIcon,
    title: "Block checkpoints & wake",
    body: "VolumeSnapshots checkpoint head and sub-agent PVCs during a run. After keep-alive, pods go away but volumes stay — continue remounts the same head PVC (or restores from the last snapshot).",
  },
];

export function Pillars() {
  return (
    <div className="pillars">
      {PILLARS.map((p) => (
        <div key={p.title} className="pillar">
          <span className="pillar-icon">
            <p.Icon size={24} />
          </span>
          <div className="pillar-title">{p.title}</div>
          <div className="pillar-body">{highlightVast(p.body)}</div>
        </div>
      ))}
    </div>
  );
}
