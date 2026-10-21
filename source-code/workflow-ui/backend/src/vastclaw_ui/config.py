from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

SECRET_PATH = Path("/etc/secrets/config.yaml")


class VastCredentials(BaseModel):
    access_key: str
    secret_key: str


class VastDBSettings(BaseModel):
    endpoint: str = "http://localhost:9090"
    telemetry_bucket: str = "vastclaw-telemetry"
    telemetry_schema: str = "telemetry"
    control_bucket: str = "vastclaw-control"
    control_schema: str = "vastclaw_control"
    ssl_verify: bool = False


class SandboxSettings(BaseModel):
    # Logical label for workflow workspace paths in UI/metadata.
    department_view_root: str = "/vastclaw/departments"
    storage_request: str = "10Gi"
    in_cluster: bool = True
    storage_class: str = ""
    # PVC access mode. Block CSI (NVMe/TCP) is single-writer => ReadWriteOnce.
    access_mode: str = "ReadWriteOnce"
    fs_group: int = 10002
    clone_subagents_from_head: bool = True
    # Write run context to the head PVC before fan-out so clones inherit workspace seed.
    seed_head_before_fanout: bool = True
    # Create a VolumeSnapshot of the head PVC immediately before sub-agent fan-out.
    checkpoint_before_fanout: bool = True
    checkpoint_on_subagent_complete: bool = True
    wake_on_continue: bool = True
    # VolumeSnapshotClass for checkpoints. Empty => "<storage_class>-snapshot".
    snapshot_class: str = ""
    max_concurrent_snapshots: int = 2
    snapshot_max_retries: int = 4
    snapshot_retry_backoff_seconds: float = 5.0
    snapshot_ready_timeout_seconds: float = 180.0


class S3Settings(BaseModel):
    endpoint: str = "http://localhost:9000"
    ssl_verify: bool = False


class ModelSettings(BaseModel):
    enabled: bool = True
    base_url: str = "https://integrate.api.nvidia.com/v1"
    api_key: str = ""
    model: str = "meta/llama-3.1-8b-instruct"
    temperature: float = 0.2
    max_tokens: int = 1024
    request_timeout_seconds: float = 60.0
    max_retries: int = 5
    retry_backoff_seconds: float = 2.0
    embed_model: str = "nvidia/nv-embedqa-e5-v5"
    embed_dim: int = 1024
    embed_base_url: str = ""
    embed_api_key: str = ""
    allow_anonymous: bool = False

    @property
    def effective_embed_base_url(self) -> str:
        return self.embed_base_url or self.base_url

    @property
    def effective_embed_api_key(self) -> str:
        if self.embed_base_url and self.embed_base_url != self.base_url:
            return self.embed_api_key
        return self.embed_api_key or self.api_key


class McpSettings(BaseModel):
    enabled: bool = True
    base_url_template: str = (
        "http://vastclaw-mcp-{department}.vastclaw-{department}.svc.cluster.local:8080"
    )
    request_timeout_seconds: float = 30.0
    rag_top_k: int = 6


class OpenClawSettings(BaseModel):
    state_dir: str = ""
    agent_id: str = "main"
    # OpenClaw model ref. Empty => derived as "nvidia/<model.model>" from ModelSettings.
    model_primary: str = ""
    tool_profile: str = "coding"
    timeout_seconds: float = 600.0
    max_attempts: int = 3


class AgentSandboxSettings(BaseModel):
    api_group: str = "agents.x-k8s.io"
    api_version: str = "v1beta1"
    plural: str = "sandboxes"
    blueprint_configmap: str = "vastclaw-nemoclaw-blueprint"
    gateway_endpoint_template: str = "os-{department}.{ns}.svc.cluster.local:8080"
    gateway_workspace: str = "default"
    gateway_timeout_seconds: float = 60.0
    gateway_ready_timeout_seconds: float = 300.0
    keep_alive_seconds: int = 900


class RuntimeSettings(BaseModel):
    mode: str = "kubernetes"
    agent_image: str = (
        "registry.example.com/vast-claw/vc-backend:latest"
    )
    max_subagents: int = 4
    require_plan_approval: bool = True
    # Quick Q&A on finished workflows (no plan / approval / sandboxes).
    followup_qa_enabled: bool = True
    job_ttl_seconds: int = 900
    poll_interval_seconds: float = 4.0
    run_timeout_seconds: float = 600.0
    workspace_mount: str = "/workspace"


class UserRecord(BaseModel):
    department: str
    is_admin: bool = False
    local: bool = False
    password: str | None = None


class AuthSettings(BaseModel):
    jwt_secret: str = "change-me"
    jwt_ttl_seconds: int = 28800
    vms_host: str = ""
    vms_port: int = 443
    tenant_name: str = "default"
    users: dict[str, UserRecord] = Field(default_factory=dict)


class ExamplePrompt(BaseModel):
    title: str
    prompt: str = ""


class DepartmentPlanningSettings(BaseModel):
    # Prepended before the live corpus hint in the planner user message.
    context_preamble: str = ""
    lead_instructions: str = ""


class DepartmentSynthesisSettings(BaseModel):
    # Full synthesis system instructions (JSON report contract).
    system_instructions: str = ""
    findings_label: str = ""


class DepartmentSettings(BaseModel):
    display_name: str = ""
    persona: str = ""
    # OpenClaw MCP toolFilter.include for this department (empty => sensible default).
    mcp_tool_filter: list[str] = Field(default_factory=list)
    ui_features: list[str] = Field(default_factory=list)
    example_prompts: list[ExamplePrompt] = Field(default_factory=list)
    evidence_type: str = "transcript"
    # Sub-agents pre-fetch transcript grounding with query embeddings.
    grounding_mode: str = ""
    # Workspace doc template: "sales_calls" | "generic".
    workspace_kind: str = ""
    planning: DepartmentPlanningSettings = Field(default_factory=DepartmentPlanningSettings)
    synthesis: DepartmentSynthesisSettings = Field(default_factory=DepartmentSynthesisSettings)


_DEFAULT_PERSONAS: dict[str, str] = {
    "sales": (
        "You are an earnings-call intelligence analyst. You read public company "
        "earnings-call transcripts and, per company and sector, surface competitive "
        "pressure, demand signals, product/roadmap themes, and buyer-relevant risks — "
        "and the single most important next step a sales or account team should take."
    ),
    "finance": (
        "You are a finance analyst embedded in the revenue organization. You read the "
        "same public earnings-call transcripts through a deal-economics lens: guidance, "
        "margin, pricing power, cost pressure, capital allocation, and "
        "revenue-recognition risk. Per company and sector, you surface the financial "
        "risks and the single most important commercial action to protect margin."
    ),
}
_GENERIC_PERSONA = (
    "You are a specialist earnings-call intelligence analyst who grounds every finding "
    "in real public company earnings transcripts and returns specific, actionable next steps."
)

_DEFAULT_MCP_TOOLS: dict[str, list[str]] = {
    "transcript": ["transcript_search", "similar_calls", "list_call_metadata"],
}

_DEFAULT_WORKSPACE_KIND: dict[str, str] = {
    "transcript": "sales_calls",
}

_DEFAULT_GROUNDING_MODE: dict[str, str] = {
    "transcript": "embedding",
}


class Settings(BaseModel):
    vast: VastCredentials
    vdb: VastDBSettings = Field(default_factory=VastDBSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    openclaw: OpenClawSettings = Field(default_factory=OpenClawSettings)
    agent_sandbox: AgentSandboxSettings = Field(default_factory=AgentSandboxSettings)
    departments: dict[str, DepartmentSettings] = Field(default_factory=dict)
    department_role_arn_template: str = "arn:vast::default:role/vastclaw-{department}-irsa"

    def persona(self, department: str) -> str:
        record = self.departments.get(department)
        if record and record.persona.strip():
            return record.persona.strip()
        return _DEFAULT_PERSONAS.get(department, _GENERIC_PERSONA)

    def department_config(self, department: str) -> DepartmentSettings:
        """Resolved per-department pack with defaults filled in."""
        raw = self.departments.get(department) or DepartmentSettings()
        evidence = (raw.evidence_type or "transcript").strip().lower()
        grounding = (raw.grounding_mode or _DEFAULT_GROUNDING_MODE.get(evidence, "embedding")).strip()
        workspace = (raw.workspace_kind or _DEFAULT_WORKSPACE_KIND.get(evidence, "generic")).strip()
        tools = list(raw.mcp_tool_filter) if raw.mcp_tool_filter else list(
            _DEFAULT_MCP_TOOLS.get(evidence, _DEFAULT_MCP_TOOLS["transcript"])
        )
        return DepartmentSettings(
            display_name=raw.display_name,
            persona=raw.persona,
            mcp_tool_filter=tools,
            ui_features=list(raw.ui_features),
            example_prompts=list(raw.example_prompts),
            evidence_type=evidence,
            grounding_mode=grounding,
            workspace_kind=workspace,
            planning=raw.planning,
            synthesis=raw.synthesis,
        )

    def mcp_tools_for(self, department: str) -> list[str]:
        return self.department_config(department).mcp_tool_filter

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> Settings:
        resolved = path or Path(os.environ.get("VASTCLAW_CONFIG", str(SECRET_PATH)))
        return cls.model_validate(yaml.safe_load(resolved.read_text()))
