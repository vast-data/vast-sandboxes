
export const STATUS_COLORS: Record<string, string> = {
  ok: "#54e7de",
  completed: "#54e7de",
  running: "#73c8fd",
  started: "#73c8fd",
  error: "#e86f6f",
};

export const STATUS_FALLBACK = "#8bafdb";

export function statusColor(status: string): string {
  return STATUS_COLORS[status] ?? STATUS_FALLBACK;
}

// DAG canvas palette (kept here rather than inline in the ReactFlow JSX).
export const GRAPH = {
  // Turbo DAG canvas: near-black background with faint brand-blue dots.
  turboDot: "#1b2b4d",
};

// A workflow/run is "active" (worth polling fast) while in these states.
const ACTIVE_STATUSES = new Set(["running", "started", "queued", "pending", "in_progress"]);

export function isActive(status?: string | null): boolean {
  return !!status && ACTIVE_STATUSES.has(status);
}
