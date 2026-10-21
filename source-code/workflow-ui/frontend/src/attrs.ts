import type { ArtifactFile, CitedEvidence, Evidence, GraphNode } from "./types";
import type { Metric } from "./components/charts";

export function jsonArr<T = unknown>(raw?: string): T[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

export interface PlanItem {
  name: string;
  focus: string;
  rationale: string;
}

export interface Highlight {
  insight: string;
  source: string;
}

export interface CustomerAction {
  customer: string;
  stage: string;
  action: string;
  source: string;
}

export function customerActions(node: GraphNode | null | undefined): CustomerAction[] {
  return jsonArr<CustomerAction>(node?.attributes.customer_actions_json);
}

export function shortModelName(model: string): string {
  const t = (model || "").trim();
  if (!t) return "unknown";
  const slash = t.lastIndexOf("/");
  return slash >= 0 ? t.slice(slash + 1) : t;
}

/** "https://integrate.api.nvidia.com/v1" -> "integrate.api.nvidia.com/v1" — the
 *  scheme is noise when the point is which host serves the endpoint. */
export function endpointLabel(url: string): string {
  const t = (url || "").trim();
  if (!t) return "not configured";
  return t.replace(/^https?:\/\//, "").replace(/\/+$/, "");
}

/** Hosted NGC vs an endpoint you run. Drives the "self-hosted" tag, so it keys off
 *  the NVIDIA domain rather than guessing from private IP ranges. */
export function isHostedNvidia(url: string): boolean {
  return /(^|\/\/|\.)nvidia\.com(\/|:|$)/i.test(url || "");
}

export function workflowProducedBy(nodes: GraphNode[], fallback = ""): string {
  const summary = nodes.find((n) => n.attributes.summary);
  if (summary) {
    return summary.attributes.produced_by || summary.attributes.model || fallback;
  }
  const lead = nodes.find((n) => n.agent_name === "research-lead");
  return lead?.attributes.model || fallback;
}

export function summaryCitations(node: GraphNode | null | undefined): CitedEvidence[] {
  return jsonArr<CitedEvidence>(node?.attributes.citations_json);
}

export function evidenceByAgent(subAgents: GraphNode[]): Map<string, Evidence[]> {
  const map = new Map<string, Evidence[]>();
  for (const n of subAgents) {
    const items = evidence(n);
    if (items.length) map.set(n.agent_name, items);
  }
  return map;
}

export function aggregateEvidence(subAgents: GraphNode[]): Evidence[] {
  const seen = new Set<string>();
  const out: Evidence[] = [];
  for (const n of subAgents) {
    for (const e of evidence(n)) {
      const key = `${e.engagement_id}|${e.text.slice(0, 96)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(e);
    }
  }
  return out;
}

export function citationsForSource(
  byAgent: Map<string, Evidence[]>,
  source: string,
): Evidence[] {
  return byAgent.get(source) ?? [];
}

export function metrics(node: GraphNode): Metric[] {
  return jsonArr<Metric>(node.attributes.metrics_json);
}

export function keyFindings(node: GraphNode): string[] {
  return jsonArr<string>(node.attributes.key_findings_json);
}

export function considerations(node: GraphNode): string[] {
  return jsonArr<string>(node.attributes.considerations_json);
}

export function sources(node: GraphNode): string[] {
  return jsonArr<string>(node.attributes.sources_json);
}

export function confidence(node: GraphNode): number | null {
  const raw = node.attributes.confidence;
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

export function isSubAgent(node: GraphNode): boolean {
  return node.kind === "agent" && node.agent_name !== "research-lead";
}

export function isRetrieval(node: GraphNode): boolean {
  return node.attributes.phase === "retrieval" || !!node.attributes.evidence_json;
}

export function evidence(node: GraphNode): Evidence[] {
  return jsonArr<Evidence>(node.attributes.evidence_json);
}

// ---- Real artifacts + the VAST sandbox that produced them ----
export function artifacts(node: GraphNode): ArtifactFile[] {
  return jsonArr<ArtifactFile>(node.attributes.artifacts_json);
}

export interface SandboxInfo {
  pvc: string;
  storageClass: string;
  accessMode: string;
  pod: string;
  mount: string;
  isBlock: boolean; // NVMe/TCP block CSI, per-agent isolated (RWO)
  cloneSourcePvc: string;
  checkpointSnapshot: string;
}

export function sandboxInfo(node: GraphNode): SandboxInfo {
  const a = node.attributes;
  const storageClass = a.storage_class ?? "";
  const accessMode = a.access_mode ?? "";
  const isBlock = /block/i.test(storageClass) || accessMode === "ReadWriteOnce";
  return {
    pvc: a.pvc_name ?? "",
    storageClass,
    accessMode,
    pod: a.pod ?? "",
    mount: a.mount_path ?? "",
    isBlock,
    cloneSourcePvc: a.clone_source_pvc ?? "",
    checkpointSnapshot: a.checkpoint_snapshot ?? "",
  };
}

export function humanBytes(n: number): string {
  if (!n || n <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

// ---- Run cost / token accounting (client-side aggregation over the DAG) ----
export interface RunCost {
  promptTokens: number;
  completionTokens: number;
  embedTokens: number;
  totalTokens: number;
  toolCalls: number;
  retrievedSnippets: number;
  estUsd: number;
}

const USD_PER_1K_LLM = 0.001;
const USD_PER_1K_EMBED = 0.00002;

export function runCost(nodes: GraphNode[]): RunCost {
  let promptTokens = 0;
  let completionTokens = 0;
  let embedTokens = 0;
  let toolCalls = 0;
  let retrievedSnippets = 0;
  for (const n of nodes) {
    const a = n.attributes;
    promptTokens += Number(a.tokens_prompt ?? 0) || 0;
    completionTokens += Number(a.tokens_completion ?? 0) || 0;
    embedTokens += Number(a.tokens_embed ?? 0) || 0;
    if (n.kind === "tool" || isRetrieval(n)) toolCalls += 1;
    retrievedSnippets += Number(a.hits ?? 0) || 0;
  }
  const totalTokens = promptTokens + completionTokens + embedTokens;
  const estUsd =
    ((promptTokens + completionTokens) / 1000) * USD_PER_1K_LLM +
    (embedTokens / 1000) * USD_PER_1K_EMBED;
  return { promptTokens, completionTokens, embedTokens, totalTokens, toolCalls, retrievedSnippets, estUsd };
}

export function cleanSummary(raw?: string): string {
  if (!raw) return "";
  const t = raw.trim();
  if (t.startsWith("{")) {
    try {
      const obj = JSON.parse(t);
      if (obj && typeof obj.summary === "string") return obj.summary;
    } catch {
      /* not JSON — render as-is */
    }
  }
  return raw;
}
