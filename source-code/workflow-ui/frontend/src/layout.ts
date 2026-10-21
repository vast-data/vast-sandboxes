import type { Edge, Node } from "reactflow";
import type { GraphNode, WorkflowGraph } from "./types";

export type TurboIcon = "lead" | "agent" | "retrieval" | "summary" | "storage";

/**
 * Which side of the orchestration split a node belongs to. The lead runs in-process
 * in the backend (no sandbox pod); sub-agents each run as a NemoClaw/OpenClaw agent
 * inside an OpenShell sandbox on a cloned VAST PVC. Rendered as a badge so the DAG
 * never implies the two are the same kind of thing.
 */
export type TurboRole = "lead" | "subagent" | null;

// Data payload consumed by the custom <TurboNode/> renderer.
export interface TurboData {
  title: string;
  subline: string;
  fullTitle: string;
  fullSubline: string;
  status: string;
  icon: TurboIcon;
  role: TurboRole;
  selected: boolean;
}

const LEAD_AGENTS = new Set(["research-lead", "synthesis"]);

export function isLeadAgent(name: string): boolean {
  return LEAD_AGENTS.has(name);
}

/** Only the two agent kinds get a role badge; tool/storage/internal steps get none. */
export function roleFor(n: GraphNode): TurboRole {
  if (n.kind !== "agent") return null;
  return isLeadAgent(n.agent_name) ? "lead" : "subagent";
}

const LABEL_ALIASES: Record<string, string> = {
  "plan-research": "Plan",
  "await-approval": "Await approval",
  "plan-approved": "Plan approved",
  "synthesize-report": "Synthesize",
  "storage-checkpoint": "Checkpoint",
  "Wake volumes": "Wake volumes",
  transcript_search: "Search calls",
  "openclaw-failed": "Run agent (failed)",
};

function truncate(text: string, max: number): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max - 1)}…`;
}

function fit(text: string, max: number, compact: boolean): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return compact ? truncate(normalized, max) : normalized;
}

function prettyAgent(name: string): string {
  if (name === "research-lead") return "Research lead";
  return name.replace(/-/g, " ");
}

/** Map raw span names (old + new) to short DAG titles. */
export function prettySpanLabel(label: string): string {
  const raw = (label || "").trim();
  if (!raw) return "Step";
  if (LABEL_ALIASES[raw]) return LABEL_ALIASES[raw];
  if (raw === "Wake volumes" || /^wake\b/i.test(raw)) return "Wake volumes";
  if (raw.startsWith("research-lead:")) return "Research lead";
  if (raw.startsWith("subagent:")) return raw.slice("subagent:".length).trim() || "Sub-agent";
  if (raw.startsWith("openclaw:")) return "Run agent";
  if (/^transcript_search\b/i.test(raw)) return "Search calls";
  return raw;
}

function isStorageNode(n: GraphNode): boolean {
  return (
    n.attributes.phase === "storage" ||
    /checkpoint|wake volumes/i.test(n.label) ||
    !!n.attributes.checkpoint_snapshot ||
    n.attributes.label === "wake-on-continue"
  );
}

/** True when the scoped (latest) run remounted/restored volumes after sandboxes slept. */
export function runWokeVolumes(nodes: GraphNode[]): boolean {
  return nodes.some(
    (n) =>
      n.attributes.label === "wake-on-continue" ||
      /wake volumes/i.test(n.label) ||
      (n.attributes.phase === "storage" && /wake/i.test(n.label)),
  );
}

function iconFor(n: GraphNode): TurboIcon {
  if (isStorageNode(n)) return "storage";
  if (n.kind === "tool" || n.attributes.phase === "retrieval" || n.attributes.evidence_json)
    return "retrieval";
  if (n.attributes.summary || n.attributes.phase === "synthesize") return "summary";
  if (n.agent_name === "research-lead" && n.kind === "agent") return "lead";
  return "agent";
}

/**
 * Title = what happened (short). Subline = who / focus.
 * Avoids the old pattern where every lead child showed title "research-lead".
 */
export function displayFor(
  n: GraphNode,
  compactSubline = true,
): { title: string; subline: string } {
  const label = prettySpanLabel(n.label);

  if (n.kind === "agent" && n.agent_name === "research-lead") {
    return {
      title: n.attributes.lead_title || n.attributes.role_title || "Research lead",
      subline: fit(
        n.attributes.department_display
          ? `${n.attributes.department_display} · ${n.attributes.input || "Coordinator"}`
          : n.attributes.input || "Coordinator",
        52,
        compactSubline,
      ),
    };
  }

  if (n.kind === "agent") {
    const role = n.attributes.role_title || n.attributes.department_display || "";
    return {
      title: prettyAgent(n.agent_name || label),
      subline: fit(
        role
          ? `${role}: ${n.attributes.input || "Research"}`
          : n.attributes.input || n.attributes.decision || "Research",
        52,
        compactSubline,
      ),
    };
  }

  if (isStorageNode(n)) {
    const title =
      /wake/i.test(n.label) || n.attributes.label === "wake-on-continue"
        ? "Wake volumes"
        : /pre-fanout|head/i.test(n.attributes.label || n.label) || /head/i.test(label)
          ? "Checkpoint head"
          : /workspace|subagent-complete|complete/i.test(n.attributes.label || n.label) ||
              /workspace/i.test(label)
            ? "Checkpoint workspace"
            : label.startsWith("Checkpoint")
              ? label
              : "Checkpoint";
    return {
      title,
      subline: fit(
        n.attributes.head_action
          ? `head: ${n.attributes.head_action}`
          : n.attributes.sandbox_pvc ||
              n.attributes.checkpoint_snapshot ||
              n.attributes.head_snapshot ||
              prettyAgent(n.agent_name),
        40,
        compactSubline,
      ),
    };
  }

  if (n.kind === "tool" || n.attributes.phase === "retrieval") {
    const tool = n.attributes.primary_search_tool || n.attributes.tool || "";
    return {
      title: label,
      subline: fit(
        [prettyAgent(n.agent_name), tool].filter(Boolean).join(" · "),
        48,
        compactSubline,
      ),
    };
  }

  // Plan / await / synthesize / other internal steps
  return {
    title: label,
    subline: fit(
      n.attributes.department_display ||
        n.attributes.role_title ||
        (n.agent_name === "research-lead" ? "Lead" : prettyAgent(n.agent_name)),
      40,
      compactSubline,
    ),
  };
}

export function toFlow(
  graph: WorkflowGraph,
  selectedId?: string | null,
): { nodes: Node[]; edges: Edge[] } {
  const childrenOf = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const edge of graph.edges) {
    childrenOf.set(edge.source, [...(childrenOf.get(edge.source) ?? []), edge.target]);
    hasParent.add(edge.target);
  }

  const depth = new Map<string, number>();
  const roots = graph.nodes.filter((n) => !hasParent.has(n.id)).map((n) => n.id);
  const queue: string[] = [...roots];
  roots.forEach((id) => depth.set(id, 0));
  while (queue.length > 0) {
    const current = queue.shift() as string;
    const level = depth.get(current) ?? 0;
    for (const child of childrenOf.get(current) ?? []) {
      if (!depth.has(child)) {
        depth.set(child, level + 1);
        queue.push(child);
      }
    }
  }

  const rowCounter = new Map<number, number>();
  const nodes: Node<TurboData>[] = graph.nodes.map((n) => {
    const level = depth.get(n.id) ?? 0;
    const row = rowCounter.get(level) ?? 0;
    rowCounter.set(level, row + 1);
    const { title, subline } = displayFor(n);
    const { title: fullTitle, subline: fullSubline } = displayFor(n, false);
    return {
      id: n.id,
      type: "turbo",
      position: { x: level * 280, y: row * 120 },
      data: {
        title,
        subline,
        fullTitle,
        fullSubline,
        status: n.status,
        icon: iconFor(n),
        role: roleFor(n),
        selected: n.id === selectedId,
      },
    };
  });

  const edges: Edge[] = graph.edges.map((e, index) => ({
    id: `e-${index}`,
    source: e.source,
    target: e.target,
    type: "turbo",
    markerEnd: "url(#vast-edge-circle)",
  }));

  return { nodes, edges };
}
