export interface ExamplePrompt {
  title: string;
  prompt: string;
}

export interface User {
  username: string;
  department: string;
  is_admin: boolean;
  department_display?: string;
  persona?: string;
  ui_features?: string[];
  example_prompts?: ExamplePrompt[];
  evidence_type?: string;
  mcp_tools?: string[];
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface WorkflowSummary {
  workflow_id: string;
  department: string;
  root_agent: string;
  user: string;
  agent_count: number;
  // Sandboxed sub-agents only; the research-lead is not counted (it runs in-process).
  subagent_count: number;
  status: string;
}

export interface GraphNode {
  id: string;
  label: string;
  agent_name: string;
  kind: string;
  status: string;
  start_time: string;
  end_time: string;
  attributes: Record<string, string>;
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface WorkflowGraph {
  workflow_id: string;
  department: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Workflow {
  workflow_id: string;
  department: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  status: string;
  sandbox_view_path: string;
  pvc_name: string;
  last_run_id: string;
}

export interface WorkflowTurn {
  turn_id: string;
  workflow_id: string;
  seq: number;
  user: string;
  department: string;
  prompt_text: string;
  created_at: string;
  run_id: string;
  status: string;
}

export interface QaMessage {
  turn_id: string;
  run_id: string;
  seq: number;
  user: string;
  question: string;
  answer: string;
  // JSON string of corpus snippets (Evidence[]) the answer cited; parse with jsonArr.
  citations_json: string;
  status: string; // running | completed | degraded | error
  created_at: string;
  produced_by: string;
  degraded: boolean;
}

export interface WorkflowDetail {
  workflow: Workflow;
  turns: WorkflowTurn[];
  graph: WorkflowGraph;
  storage: WorkflowStorageState | null;
  wake_hint: WorkflowWakeHint | null;
  qa_messages: QaMessage[];
}

export interface WorkflowWakeHint {
  sandboxes_alive: boolean;
  continue_will_wake: boolean;
  head_pvc_present: boolean;
  retained_pvc_count: number;
  snapshot_count: number;
  last_checkpoint: string;
  message: string;
}

export interface VolumeSnapshotInfo {
  name: string;
  source_pvc: string;
  ready: boolean;
  created_at: string;
  run_id: string;
  snapshot_class: string;
}

export interface WorkflowPvcInfo {
  name: string;
  role: string;
  subagent: string;
  clone_source_pvc: string;
  storage_class: string;
  access_mode: string;
  status: string;
  capacity: string;
}

export interface WorkflowStorageState {
  workflow_id: string;
  department: string;
  head_pvc: string;
  pvcs: WorkflowPvcInfo[];
  snapshots: VolumeSnapshotInfo[];
  clone_enabled: boolean;
  checkpoint_enabled: boolean;
  checkpoint_on_subagent_complete: boolean;
  wake_on_continue: boolean;
  snapshot_class: string;
  last_checkpoint: string;
}

export interface DeleteWorkflowResult {
  workflow_id: string;
  department: string;
  deleted: boolean;
  sandboxes: string[];
  jobs: string[];
  snapshots: string[];
  pvcs: string[];
}

export interface ReportMeta {
  report_id: string;
  workflow_id: string;
  department: string;
  version: number;
  title: string;
  created_by: string;
  created_at: string;
  size: number;
}

export interface DepartmentStat {
  department: string;
  workflows: number;
  running: number;
  completed: number;
  error: number;
  tokens: number;
  agents: number;
  tool_calls: number;
}

export interface UserStat {
  username: string;
  department: string;
  is_admin: boolean;
  workflows: number;
  tokens: number;
  last_active: string;
}

export interface ActivityItem {
  workflow_id: string;
  title: string;
  user: string;
  department: string;
  status: string;
  at: string;
}

export interface AgentStat {
  name: string;
  department: string;
  tokens: number;
  spans: number;
  tool_calls: number;
}

export interface NameCount {
  name: string;
  count: number;
}

export interface AdminStats {
  workflows: number;
  running: number;
  completed: number;
  error: number;
  runs: number;
  agents_served: number;
  tool_calls: number;
  tokens_used: number;
  reports: number;
  avg_agents_per_workflow: number;
  by_department: DepartmentStat[];
  top_users: UserStat[];
  top_agents: AgentStat[];
  tool_breakdown: NameCount[];
  activity_by_day: NameCount[];
  recent: ActivityItem[];
}

export interface DeploymentHealth {
  name: string;
  ready: number;
  desired: number;
}

export interface DepartmentHealth {
  department: string;
  namespace: string;
  healthy: boolean;
  deployments: DeploymentHealth[];
  error: string;
}

export interface RestartResult {
  department: string;
  restarted: string[];
}

export interface ResetResult {
  departments: string[];
  pvcs_deleted: string[];
  tables_cleared: string[];
}

export interface AdminAction {
  action_id: string;
  actor: string;
  department: string;
  action: string;
  target: string;
  ts: string;
  result: string;
}

export interface ToolInfo {
  name: string;
  description: string;
}

export interface SandboxStat {
  department: string;
  namespace: string;
  pvc_total: number;
  pvc_bound: number;
  pvc_pending: number;
  head_pvcs: number;
  subagent_pvcs: number;
  cloned_pvcs: number;
  restored_pvcs: number;
  volume_snapshots: number;
  snapshots_ready: number;
  capacity_bytes: number;
  capacity_human: string;
  storage_class: string;
  active_agents: number;
  workflows: number;
  error: string;
}

export interface WorkflowSandboxRow {
  workflow_id: string;
  title: string;
  status: string;
  head_pvc: string;
  pvc_total: number;
  pvc_bound: number;
  pvc_pending: number;
  subagent_pvcs: number;
  cloned_pvcs: number;
  restored_pvcs: number;
  volume_snapshots: number;
  snapshots_ready: number;
  active_agents: number;
  capacity_bytes: number;
  capacity_human: string;
}

export interface DepartmentStorageInventory {
  department: string;
  namespace: string;
  summary: SandboxStat;
  workflows: WorkflowSandboxRow[];
  error: string;
}

export interface AdminStorageView {
  departments: DepartmentStorageInventory[];
  totals: SandboxStat;
}

export interface AgentLiveness {
  slug: string;
  alive: boolean;
  pod: string;
  phase: string;
}

export interface ArtifactFile {
  name: string;
  rel: string;
  path: string;
  bytes: number;
  kind: string;
  content?: string;
}

export interface Evidence {
  engagement_id?: string;
  customer?: string;
  opportunity?: string;
  sales_stage?: string;
  speaker_type?: string;
  call_date?: string;
  distance?: number | null;
  text: string;
  // VastDB table this snippet was read from. Empty on older runs.
  table?: string;
}

export interface CitedEvidence extends Evidence {
  source?: string;
}

export interface PlatformModelInfo {
  provider: string;
  enabled: boolean;
  // Backend completions: research-lead planning + final synthesis.
  model: string;
  base_url: string;
  // The OpenClaw sub-agent loop.
  openclaw_primary: string;
  openclaw_base_url: string;
  // Query-side embeddings for RAG retrieval.
  embed_model: string;
  embed_base_url: string;
  embed_dim: number;
  // True when embeddings are NOT on the generation endpoint.
  split_endpoints: boolean;
}
