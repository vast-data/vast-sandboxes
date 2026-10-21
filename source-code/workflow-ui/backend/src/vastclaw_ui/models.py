from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field


class User(BaseModel):
    username: str
    department: str
    is_admin: bool = False
    # Presentation-only, populated by /api/v1/me (NOT carried in the JWT).
    department_display: str = ""
    persona: str = ""
    ui_features: list[str] = Field(default_factory=list)
    example_prompts: list[dict[str, str]] = Field(default_factory=list)
    evidence_type: str = "transcript"
    mcp_tools: list[str] = Field(default_factory=list)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: User


class WorkflowSummary(BaseModel):
    workflow_id: str
    department: str
    root_agent: str
    user: str
    span_count: int
    agent_count: int
    subagent_count: int = 0
    started_at: str
    status: str


class GraphNode(BaseModel):
    id: str
    label: str
    agent_name: str
    kind: str
    status: str
    start_time: str
    end_time: str
    attributes: dict[str, str]


class GraphEdge(BaseModel):
    source: str
    target: str


class WorkflowGraph(BaseModel):
    workflow_id: str
    department: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Workflow(BaseModel):
    workflow_id: str
    department: str
    title: str
    created_by: str
    created_at: str
    updated_at: str
    status: str = "running"
    sandbox_view_path: str = ""
    pvc_name: str = ""
    last_run_id: str = ""


class WorkflowTurn(BaseModel):
    turn_id: str
    workflow_id: str
    seq: int
    user: str
    department: str
    prompt_text: str
    created_at: str
    run_id: str = ""
    status: str = "submitted"


class Run(BaseModel):
    run_id: str
    workflow_id: str
    turn_id: str
    department_role: str
    started_at: str
    ended_at: str = ""
    status: str = "started"


class ReportMeta(BaseModel):
    report_id: str
    workflow_id: str
    department: str
    version: int
    title: str
    created_by: str
    created_at: str
    size: int = 0


class CreateWorkflowRequest(BaseModel):
    title: str
    prompt: str
    auto_approve_plan: bool = False


class AddTurnRequest(BaseModel):
    prompt: str
    auto_approve_plan: bool = False
    # "agentic" = full research turn; "qa" = Quick Q&A about the existing report.
    mode: str = "agentic"


class PlanItemInput(BaseModel):
    name: str
    focus: str
    rationale: str = ""


class ApprovePlanRequest(BaseModel):
    items: list[PlanItemInput]


class VolumeSnapshotInfo(BaseModel):
    name: str
    source_pvc: str
    ready: bool = False
    created_at: str = ""
    run_id: str = ""
    snapshot_class: str = ""


class WorkflowPvcInfo(BaseModel):
    name: str
    role: str = ""  # head | subagent
    subagent: str = ""
    clone_source_pvc: str = ""
    storage_class: str = ""
    access_mode: str = ""
    status: str = ""
    capacity: str = ""


class WorkflowStorageState(BaseModel):
    workflow_id: str
    department: str
    head_pvc: str
    pvcs: list[WorkflowPvcInfo] = Field(default_factory=list)
    snapshots: list[VolumeSnapshotInfo] = Field(default_factory=list)
    clone_enabled: bool = True
    checkpoint_enabled: bool = True  # pre-fan-out head checkpoint
    checkpoint_on_subagent_complete: bool = True
    wake_on_continue: bool = True
    snapshot_class: str = ""
    last_checkpoint: str = ""


class CheckpointRequest(BaseModel):
    pvc_name: str = ""  # default: workflow head PVC
    label: str = ""


class RestoreVolumeRequest(BaseModel):
    snapshot_name: str
    pvc_name: str = ""  # auto-generated when empty


class WorkflowWakeHint(BaseModel):
    """UI hint: continuing this research will wake retained block volumes."""

    sandboxes_alive: bool = False
    continue_will_wake: bool = False
    head_pvc_present: bool = False
    retained_pvc_count: int = 0
    snapshot_count: int = 0
    last_checkpoint: str = ""
    message: str = ""


class QaMessage(BaseModel):
    """One Quick Q&A exchange (kind=\"qa\" span) for a finished workflow."""

    turn_id: str = ""
    run_id: str
    seq: int = 0
    user: str = ""
    question: str = ""
    answer: str = ""
    # Corpus snippets as JSON (same shape as evidence_json elsewhere).
    citations_json: str = ""
    status: str = "running"  # running | completed | degraded | error
    created_at: str = ""
    produced_by: str = ""
    degraded: bool = False


class WorkflowDetail(BaseModel):
    workflow: Workflow
    turns: list[WorkflowTurn]
    graph: WorkflowGraph
    storage: WorkflowStorageState | None = None
    wake_hint: WorkflowWakeHint | None = None
    qa_messages: list[QaMessage] = Field(default_factory=list)


class DepartmentStat(BaseModel):
    department: str
    workflows: int = 0
    running: int = 0
    completed: int = 0
    error: int = 0
    tokens: int = 0
    agents: int = 0
    tool_calls: int = 0


class UserStat(BaseModel):
    username: str
    department: str
    is_admin: bool = False
    workflows: int = 0
    tokens: int = 0
    last_active: str = ""


class ActivityItem(BaseModel):
    workflow_id: str
    title: str
    user: str
    department: str
    status: str
    at: str


class AgentStat(BaseModel):
    name: str
    department: str = ""
    tokens: int = 0
    spans: int = 0
    tool_calls: int = 0


class NameCount(BaseModel):
    name: str
    count: int = 0


class PlatformModelInfo(BaseModel):
    """Active models + endpoints from the mounted workflow-ui config."""

    provider: str = "NVIDIA NIM"
    enabled: bool = True
    # Backend's own completions: research-lead planning + final synthesis.
    model: str = ""
    base_url: str = ""
    # The OpenClaw sub-agent loop (its generation provider).
    openclaw_primary: str = ""
    openclaw_base_url: str = ""
    # Query-side embeddings for RAG retrieval.
    embed_model: str = ""
    embed_base_url: str = ""
    embed_dim: int = 0
    # True when embeddings are NOT on the generation endpoint.
    split_endpoints: bool = False


class AdminStats(BaseModel):
    workflows: int = 0
    running: int = 0
    completed: int = 0
    error: int = 0
    runs: int = 0
    agents_served: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    reports: int = 0
    avg_agents_per_workflow: float = 0.0
    by_department: list[DepartmentStat] = []
    top_users: list[UserStat] = []
    top_agents: list[AgentStat] = []
    tool_breakdown: list[NameCount] = []
    activity_by_day: list[NameCount] = []
    recent: list[ActivityItem] = []


class DeploymentHealth(BaseModel):
    name: str
    ready: int = 0
    desired: int = 0


class DepartmentHealth(BaseModel):
    department: str
    namespace: str
    healthy: bool = False
    deployments: list[DeploymentHealth] = []
    error: str = ""


class RestartResult(BaseModel):
    department: str
    restarted: list[str] = []


class ResetResult(BaseModel):
    departments: list[str] = []
    pvcs_deleted: list[str] = []
    tables_cleared: list[str] = []


class AdminAction(BaseModel):
    action_id: str
    actor: str
    department: str = ""
    action: str
    target: str = ""
    ts: str
    result: str = ""


class DeleteWorkflowResult(BaseModel):

    workflow_id: str
    department: str = ""
    deleted: bool = True
    sandboxes: list[str] = Field(default_factory=list)
    jobs: list[str] = Field(default_factory=list)
    snapshots: list[str] = Field(default_factory=list)
    pvcs: list[str] = Field(default_factory=list)


class ToolInfo(BaseModel):
    """One MCP tool currently exposed to the department's agents."""

    name: str
    description: str = ""


class SandboxStat(BaseModel):

    department: str
    namespace: str
    pvc_total: int = 0
    pvc_bound: int = 0
    pvc_pending: int = 0
    head_pvcs: int = 0
    subagent_pvcs: int = 0
    cloned_pvcs: int = 0          # sub-agent PVCs forked from head (zero-copy clone)
    restored_pvcs: int = 0        # PVCs created from VolumeSnapshot restore
    volume_snapshots: int = 0     # vastclaw-managed VolumeSnapshot checkpoints
    snapshots_ready: int = 0      # VolumeSnapshots with readyToUse=true
    capacity_bytes: int = 0
    capacity_human: str = "0"
    storage_class: str = ""
    active_agents: int = 0   # OpenShell sandbox pods currently running
    workflows: int = 0       # distinct workflows with a live sandbox
    error: str = ""


class WorkflowSandboxRow(BaseModel):
    """Per-workflow agent sandbox storage — one row in the admin storage inventory."""

    workflow_id: str
    title: str = ""
    status: str = ""
    head_pvc: str = ""
    pvc_total: int = 0
    pvc_bound: int = 0
    pvc_pending: int = 0
    subagent_pvcs: int = 0
    cloned_pvcs: int = 0
    restored_pvcs: int = 0
    volume_snapshots: int = 0
    snapshots_ready: int = 0
    active_agents: int = 0
    capacity_bytes: int = 0
    capacity_human: str = "0"


class DepartmentStorageInventory(BaseModel):
    """All agent sandbox volumes + snapshots for one department namespace."""

    department: str
    namespace: str
    summary: SandboxStat
    workflows: list[WorkflowSandboxRow] = Field(default_factory=list)
    error: str = ""


class AdminStorageView(BaseModel):
    """Cross-department agent storage inventory for platform admins."""

    departments: list[DepartmentStorageInventory] = Field(default_factory=list)
    totals: SandboxStat


class AgentLiveness(BaseModel):

    slug: str
    alive: bool = False
    pod: str = ""
    phase: str = ""
