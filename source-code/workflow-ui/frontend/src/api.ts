import type {
  AdminAction,
  AdminStats,
  AdminStorageView,
  AgentLiveness,
  DeleteWorkflowResult,
  DepartmentHealth,
  PlatformModelInfo,
  ReportMeta,
  ResetResult,
  RestartResult,
  SandboxStat,
  TokenResponse,
  ToolInfo,
  User,
  UserStat,
  VolumeSnapshotInfo,
  WorkflowDetail,
  WorkflowStorageState,
  WorkflowSummary,
} from "./types";

const TOKEN_KEY = "vastclaw_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * Blueprints-page launcher redirects with:
 *   ?auto-login=1&token=<jwt>&username=<user>
 * Persist the JWT before App boots, then strip the query so the token is not
 * left in the address bar / history.
 */
export function consumeBlueprintsAutoLogin(): void {
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get("auto-login") !== "1") return;
    const token = params.get("token");
    if (!token) return;
    setToken(token);
    const url = new URL(window.location.href);
    url.searchParams.delete("auto-login");
    url.searchParams.delete("token");
    url.searchParams.delete("username");
    const cleaned = `${url.pathname}${url.search}${url.hash}`;
    window.history.replaceState({}, "", cleaned);
  } catch {
    /* ignore malformed URLs */
  }
}

async function authorized<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getToken() ?? ""}`,
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (init?.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 || response.status === 403) {
    clearToken();
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    throw new Error(`request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const response = await fetch("/api/v1/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error("invalid credentials");
  }
  return (await response.json()) as TokenResponse;
}

export function getMe(): Promise<User> {
  return authorized<User>("/api/v1/me");
}

export function getPlatformModel(): Promise<PlatformModelInfo> {
  return authorized<PlatformModelInfo>("/api/v1/platform/model");
}

export function listWorkflows(): Promise<WorkflowSummary[]> {
  return authorized<WorkflowSummary[]>("/api/v1/workflows");
}

export function getWorkflowDetail(workflowId: string): Promise<WorkflowDetail> {
  return authorized<WorkflowDetail>(`/api/v1/workflows/${workflowId}/detail`);
}

export function createWorkflow(title: string, prompt: string): Promise<WorkflowDetail> {
  return authorized<WorkflowDetail>("/api/v1/workflows", {
    method: "POST",
    body: JSON.stringify({ title, prompt }),
  });
}

export function addTurn(
  workflowId: string,
  prompt: string,
  mode: "agentic" | "qa" = "agentic",
): Promise<WorkflowDetail> {
  return authorized<WorkflowDetail>(`/api/v1/workflows/${workflowId}/turns`, {
    method: "POST",
    body: JSON.stringify({ prompt, mode }),
  });
}

export async function deleteWorkflow(workflowId: string): Promise<DeleteWorkflowResult> {
  const response = await fetch(`/api/v1/workflows/${workflowId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (response.status === 401) {
    clearToken();
    throw new Error("unauthorized");
  }
  if (!response.ok) {
    let detail = `request failed: ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — keep the generic message */
    }
    throw new Error(detail);
  }
  return (await response.json()) as DeleteWorkflowResult;
}

export interface PlanItemInput {
  name: string;
  focus: string;
  rationale: string;
}

export function approvePlan(
  workflowId: string,
  items: PlanItemInput[],
): Promise<WorkflowDetail> {
  return authorized<WorkflowDetail>(`/api/v1/workflows/${workflowId}/approve`, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

export function checkpointWorkflow(
  workflowId: string,
  opts?: { pvc_name?: string; label?: string },
): Promise<VolumeSnapshotInfo> {
  return authorized<VolumeSnapshotInfo>(`/api/v1/workflows/${workflowId}/checkpoint`, {
    method: "POST",
    body: JSON.stringify(opts ?? {}),
  });
}

export function restoreVolumeFromSnapshot(
  workflowId: string,
  snapshotName: string,
  pvcName?: string,
): Promise<WorkflowStorageState> {
  return authorized<WorkflowStorageState>(`/api/v1/workflows/${workflowId}/volumes/restore`, {
    method: "POST",
    body: JSON.stringify({ snapshot_name: snapshotName, pvc_name: pvcName ?? "" }),
  });
}

export function createReport(workflowId: string): Promise<ReportMeta> {
  return authorized<ReportMeta>(`/api/v1/workflows/${workflowId}/reports`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function listReports(workflowId: string): Promise<ReportMeta[]> {
  return authorized<ReportMeta[]>(`/api/v1/workflows/${workflowId}/reports`);
}

export async function getReportBlob(workflowId: string, reportId: string): Promise<Blob> {
  const response = await fetch(`/api/v1/workflows/${workflowId}/reports/${reportId}`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!response.ok) throw new Error(`request failed: ${response.status}`);
  return await response.blob();
}

export function listTools(department?: string): Promise<ToolInfo[]> {
  const q = department ? `?department=${encodeURIComponent(department)}` : "";
  return authorized<ToolInfo[]>(`/api/v1/tools${q}`);
}

export function getSandboxes(): Promise<SandboxStat[]> {
  return authorized<SandboxStat[]>("/api/v1/sandboxes");
}

export function getWorkflowAgents(workflowId: string): Promise<AgentLiveness[]> {
  return authorized<AgentLiveness[]>(`/api/v1/workflows/${workflowId}/agents`);
}

export function agentShellUrl(workflowId: string, slug: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const token = encodeURIComponent(getToken() ?? "");
  return `${proto}//${window.location.host}/api/v1/workflows/${workflowId}/agents/${slug}/shell?token=${token}`;
}

export function getAdminStats(): Promise<AdminStats> {
  return authorized<AdminStats>("/api/v1/admin/stats");
}

export function getStats(): Promise<AdminStats> {
  return authorized<AdminStats>("/api/v1/stats");
}

export function getAdminUsers(): Promise<UserStat[]> {
  return authorized<UserStat[]>("/api/v1/admin/users");
}

export function getAdminHealth(): Promise<DepartmentHealth[]> {
  return authorized<DepartmentHealth[]>("/api/v1/admin/health");
}

export function getAdminStorage(): Promise<AdminStorageView> {
  return authorized<AdminStorageView>("/api/v1/admin/storage");
}

export function getAdminAudit(): Promise<AdminAction[]> {
  return authorized<AdminAction[]>("/api/v1/admin/audit");
}

export function restartDepartment(department: string): Promise<RestartResult> {
  return authorized<RestartResult>(`/api/v1/admin/departments/${department}/restart`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function restartPlatform(): Promise<RestartResult[]> {
  return authorized<RestartResult[]>("/api/v1/admin/restart", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function resetPlatform(): Promise<ResetResult> {
  return authorized<ResetResult>("/api/v1/admin/reset", {
    method: "POST",
    body: JSON.stringify({}),
  });
}
