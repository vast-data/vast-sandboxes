from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from vastclaw_ui.admin import compute_stats, compute_users
from vastclaw_ui.auth import (
    Authenticator,
    ConfigAuthenticator,
    InvalidCredentialsError,
    TokenService,
    VmsAuthenticator,
)
from vastclaw_ui.config import Settings
from vastclaw_ui.graph import build_graph, summarize
from vastclaw_ui.history import (
    HistoryStore,
    InMemoryHistoryStore,
    VastDBHistoryStore,
    WorkflowNotFoundError,
)
from vastclaw_ui.k8s import ClusterControl, KubernetesControl
from vastclaw_ui.models import (
    AddTurnRequest,
    AdminAction,
    AdminStats,
    AdminStorageView,
    AgentLiveness,
    ApprovePlanRequest,
    CheckpointRequest,
    CreateWorkflowRequest,
    DeleteWorkflowResult,
    DepartmentHealth,
    DepartmentStorageInventory,
    GraphNode,
    LoginRequest,
    PlatformModelInfo,
    QaMessage,
    ReportMeta,
    ResetResult,
    RestartResult,
    RestoreVolumeRequest,
    Run,
    SandboxStat,
    TokenResponse,
    ToolInfo,
    User,
    UserStat,
    VolumeSnapshotInfo,
    Workflow,
    WorkflowDetail,
    WorkflowGraph,
    WorkflowPvcInfo,
    WorkflowStorageState,
    WorkflowSummary,
    WorkflowTurn,
    WorkflowWakeHint,
)
from vastclaw_ui.openclaw_runner import _model_primary
from vastclaw_ui.rag_client import RagMcpClient
from vastclaw_ui.report import CustomerAction, ReportContent, ReportSection, render_report_pdf
from vastclaw_ui.runtime import (
    InMemoryProvisioner,
    KubernetesProvisioner,
    KubernetesRuntime,
    LocalResearchRuntime,
    NemoClawClient,
    RecordingNemoClawClient,
    RunRequest,
    Sandbox,
    SandboxProvisioner,
)
from vastclaw_ui.store import SpanSource, VastDBSpanSource

_bearer = HTTPBearer(auto_error=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _bridge_shell(browser: WebSocket, kube: object) -> None:
    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    async def kube_to_browser() -> None:
        try:
            while not stop.is_set() and kube.is_open():  # type: ignore[attr-defined]
                await loop.run_in_executor(None, kube.update, 1)  # type: ignore[attr-defined]
                if kube.peek_stdout():  # type: ignore[attr-defined]
                    await browser.send_text(kube.read_stdout())  # type: ignore[attr-defined]
                if kube.peek_stderr():  # type: ignore[attr-defined]
                    await browser.send_text(kube.read_stderr())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - any transport error ends the session
            pass
        finally:
            stop.set()
            with contextlib.suppress(Exception):
                await browser.close()

    async def browser_to_kube() -> None:
        try:
            while not stop.is_set():
                data = await browser.receive_text()
                if kube.is_open():  # type: ignore[attr-defined]
                    kube.write_stdin(data)  # type: ignore[attr-defined]
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            stop.set()
            with contextlib.suppress(Exception):
                kube.close()  # type: ignore[attr-defined]

    await asyncio.gather(kube_to_browser(), browser_to_kube())


def _json_load(raw: str) -> list:
    try:
        parsed = json.loads(raw or "[]", strict=False)
        return parsed if isinstance(parsed, list) else []
    except (ValueError, TypeError):
        return []


def _clean_summary(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            obj = json.loads(t, strict=False)
            if isinstance(obj, dict) and isinstance(obj.get("summary"), str):
                return obj["summary"]
        except (ValueError, TypeError):
            pass
    return text or ""


def _report_content(
    workflow: Workflow, nodes: list[GraphNode], version: int, created_by: str
) -> ReportContent:
    summary_node = next((n for n in nodes if n.attributes.get("summary")), None)
    summary = _clean_summary(summary_node.attributes.get("summary", "")) if summary_node else ""
    recommendation = summary_node.attributes.get("recommendation", "") if summary_node else ""
    produced_by = ""
    if summary_node:
        produced_by = (
            summary_node.attributes.get("produced_by")
            or summary_node.attributes.get("model")
            or ""
        )
    highlights = (
        _json_load(summary_node.attributes.get("highlights_json", "")) if summary_node else []
    )
    customer_actions_raw = (
        _json_load(summary_node.attributes.get("customer_actions_json", "")) if summary_node else []
    )
    customer_actions = [
        CustomerAction(
            customer=str(a.get("customer", "")),
            stage=str(a.get("stage", "")),
            action=str(a.get("action", "")),
            source=str(a.get("source", "")),
        )
        for a in customer_actions_raw
        if isinstance(a, dict) and a.get("customer") and a.get("action")
    ]
    key_findings = [
        str(h["insight"]) for h in highlights if isinstance(h, dict) and h.get("insight")
    ]
    sections: list[ReportSection] = []
    for node in nodes:
        if node.kind != "agent" or node.agent_name == "research-lead":
            continue
        findings = [
            str(x)
            for x in _json_load(node.attributes.get("key_findings_json", ""))
            if str(x).strip()
        ]
        metrics = [
            m for m in _json_load(node.attributes.get("metrics_json", "")) if isinstance(m, dict)
        ]
        if findings or metrics:
            sections.append(ReportSection(name=node.agent_name, findings=findings, metrics=metrics))
    return ReportContent(
        title=workflow.title,
        department=workflow.department,
        created_by=created_by,
        version=version,
        created_at=_now(),
        summary=summary,
        key_findings=key_findings,
        recommendation=recommendation,
        produced_by=produced_by,
        customer_actions=customer_actions,
        sections=sections,
    )


def create_app(
    settings: Settings,
    span_source: SpanSource,
    authenticator: Authenticator | None = None,
    history_store: HistoryStore | None = None,
    provisioner: SandboxProvisioner | None = None,
    nemoclaw_client: NemoClawClient | None = None,
    cluster_control: ClusterControl | None = None,
) -> FastAPI:
    app = FastAPI(title="vastclaw workflow UI")
    auth = authenticator or ConfigAuthenticator(settings.auth)
    tokens = TokenService(settings.auth)
    history: HistoryStore = history_store or InMemoryHistoryStore()
    sandboxes: SandboxProvisioner = provisioner or InMemoryProvisioner(settings.sandbox)
    nemoclaw: NemoClawClient = nemoclaw_client or RecordingNemoClawClient()
    cluster: ClusterControl = cluster_control or KubernetesControl(settings.sandbox.in_cluster)

    def current_user(
        credentials: HTTPAuthorizationCredentials = Depends(_bearer),
    ) -> User:
        try:
            return tokens.verify(credentials.credentials)
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    def admin_user(user: User = Depends(current_user)) -> User:
        if not user.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
        return user

    def scope_for(user: User) -> str | None:
        return None if user.is_admin else user.department

    def departments() -> list[str]:
        # Primary source: configured non-admin user records supply the department roster.
        depts = {
            r.department
            for r in settings.auth.users.values()
            if not r.is_admin and r.department
        }
        depts.update(settings.departments.keys())
        try:
            depts.update(w.department for w in history.list_workflows(None) if w.department)
        except Exception:
            logging.getLogger(__name__).debug(
                "department discovery from history failed", exc_info=True
            )
        return sorted(depts)

    def _all_turns(workflows: list[Workflow]) -> list[WorkflowTurn]:
        turns: list[WorkflowTurn] = []
        for wf in workflows:
            turns.extend(history.list_turns(wf.workflow_id))
        return turns

    def _reports_count(workflows: list[Workflow]) -> int:
        total = 0
        for wf in workflows:
            with contextlib.suppress(Exception):  # dashboard metric is best-effort
                total += len(history.list_reports(wf.workflow_id))
        return total

    def department_role(department: str) -> str:
        return settings.department_role_arn_template.format(department=department)

    def trigger_run(workflow: Workflow, turn: WorkflowTurn, *, auto_approve_plan: bool = False) -> None:
        run_id = turn.run_id
        started = _now()
        history.record_run(
            Run(
                run_id=run_id,
                workflow_id=workflow.workflow_id,
                turn_id=turn.turn_id,
                department_role=department_role(workflow.department),
                started_at=started,
                status="running",
            )
        )
        try:
            nemoclaw.start_run(
                RunRequest(
                    run_id=run_id,
                    workflow_id=workflow.workflow_id,
                    turn_id=turn.turn_id,
                    department=workflow.department,
                    user=turn.user,
                    prompt=turn.prompt_text,
                    sandbox=Sandbox(
                        pvc_name=workflow.pvc_name,
                        view_path=workflow.sandbox_view_path,
                    ),
                    auto_approve_plan=auto_approve_plan,
                )
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "run dispatch failed for run %s (workflow %s): %s",
                run_id,
                workflow.workflow_id,
                exc,
            )
            failed_at = _now()
            history.update_run_status(run_id, "dispatch_failed", failed_at)
            history.update_turn_status(workflow.workflow_id, turn.turn_id, "dispatch_failed")
            history.update_workflow_status(workflow.workflow_id, "error", failed_at)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    def _enrich(user: User) -> User:
        if user.is_admin:
            user.department_display = "All departments"
            user.persona = ""
            user.ui_features = []
            user.example_prompts = []
            user.evidence_type = "transcript"
            user.mcp_tools = []
        else:
            dept_cfg = settings.department_config(user.department)
            record = settings.departments.get(user.department)
            user.department_display = (
                record.display_name
                if record and record.display_name
                else user.department.title()
            )
            user.persona = settings.persona(user.department)
            user.ui_features = list(dept_cfg.ui_features)
            user.example_prompts = [
                {"title": p.title, "prompt": p.prompt} for p in dept_cfg.example_prompts
            ]
            user.evidence_type = dept_cfg.evidence_type
            user.mcp_tools = list(settings.mcp_tools_for(user.department))
        return user

    @app.post("/api/v1/login", response_model=TokenResponse)
    def login(credentials: LoginRequest) -> TokenResponse:
        try:
            user = auth.authenticate(credentials.username, credentials.password)
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        return TokenResponse(access_token=tokens.issue(user), user=_enrich(user))

    @app.get("/api/v1/me", response_model=User)
    def me(user: User = Depends(current_user)) -> User:
        return _enrich(user)

    @app.get("/api/v1/platform/model", response_model=PlatformModelInfo)
    def platform_model(_: User = Depends(current_user)) -> PlatformModelInfo:
        m = settings.model
        return PlatformModelInfo(
            provider="NVIDIA NIM",
            enabled=m.enabled,
            model=m.model,
            base_url=m.base_url,
            openclaw_primary=_model_primary(settings),
            openclaw_base_url=m.base_url,
            embed_model=m.embed_model,
            embed_base_url=m.effective_embed_base_url,
            embed_dim=m.embed_dim,
            split_endpoints=m.effective_embed_base_url != m.base_url,
        )

    @app.get("/api/v1/workflows", response_model=list[WorkflowSummary])
    def list_workflows(user: User = Depends(current_user)) -> list[WorkflowSummary]:
        scope = scope_for(user)
        # Telemetry summaries carry execution detail (span/agent counts, DAG).
        span_summaries = {s.workflow_id: s for s in summarize(span_source.fetch(scope))}
        result: dict[str, WorkflowSummary] = {}
        for wf in history.list_workflows(scope):
            sm = span_summaries.get(wf.workflow_id)
            result[wf.workflow_id] = WorkflowSummary(
                workflow_id=wf.workflow_id,
                department=wf.department,
                root_agent=sm.root_agent if sm else wf.title,
                user=wf.created_by,
                span_count=sm.span_count if sm else 0,
                agent_count=sm.agent_count if sm else 0,
                subagent_count=sm.subagent_count if sm else 0,
                started_at=wf.created_at,
                status=wf.status,
            )
        # Span-only workflows with no control row (e.g. seeded demo narrative).
        for workflow_id, sm in span_summaries.items():
            if workflow_id not in result:
                result[workflow_id] = sm
        return sorted(result.values(), key=lambda s: s.started_at, reverse=True)

    @app.post(
        "/api/v1/workflows",
        response_model=WorkflowDetail,
        status_code=status.HTTP_201_CREATED,
    )
    def create_workflow(
        request: CreateWorkflowRequest, user: User = Depends(current_user)
    ) -> WorkflowDetail:
        workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
        sandbox = sandboxes.ensure_workflow_sandbox(user.department, workflow_id)
        now = _now()
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        workflow = history.create_workflow(
            Workflow(
                workflow_id=workflow_id,
                department=user.department,
                title=request.title,
                created_by=user.username,
                created_at=now,
                updated_at=now,
                sandbox_view_path=sandbox.view_path,
                pvc_name=sandbox.pvc_name,
                last_run_id=run_id,
            )
        )
        turn = history.add_turn(
            WorkflowTurn(
                turn_id=f"turn-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                seq=history.next_seq(workflow_id),
                user=user.username,
                department=user.department,
                prompt_text=request.prompt,
                created_at=now,
                run_id=run_id,
            )
        )
        trigger_run(workflow, turn, auto_approve_plan=request.auto_approve_plan)
        return _detail(workflow, scope_for(user))

    def trigger_qa(workflow: Workflow, turn: WorkflowTurn) -> None:
        # Quick Q&A: record a run for audit; do not fan out or change last_run_id.
        run_id = turn.run_id
        started = _now()
        history.record_run(
            Run(
                run_id=run_id,
                workflow_id=workflow.workflow_id,
                turn_id=turn.turn_id,
                department_role=department_role(workflow.department),
                started_at=started,
                status="running",
            )
        )
        req = RunRequest(
            run_id=run_id,
            workflow_id=workflow.workflow_id,
            turn_id=turn.turn_id,
            department=workflow.department,
            user=turn.user,
            prompt=turn.prompt_text,
            sandbox=Sandbox(
                pvc_name=workflow.pvc_name,
                view_path=workflow.sandbox_view_path,
            ),
        )
        try:
            nemoclaw.begin_question(req)
            nemoclaw.answer_question(req)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Q&A dispatch failed for run %s (workflow %s): %s",
                run_id,
                workflow.workflow_id,
                exc,
            )
            failed_at = _now()
            history.update_run_status(run_id, "dispatch_failed", failed_at)
            history.update_turn_status(workflow.workflow_id, turn.turn_id, "dispatch_failed")

    @app.post("/api/v1/workflows/{workflow_id}/turns", response_model=WorkflowDetail)
    def add_turn(
        workflow_id: str, request: AddTurnRequest, user: User = Depends(current_user)
    ) -> WorkflowDetail:
        workflow = _require_workflow(workflow_id, scope_for(user))
        now = _now()
        run_id = f"run-{uuid.uuid4().hex[:12]}"

        # Quick Q&A about the existing report — no plan/approval/sandboxes, no set_last_run.
        if request.mode == "qa":
            if not settings.runtime.followup_qa_enabled:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="follow-up Q&A is disabled on this deployment",
                )
            if workflow.status not in ("completed", "error", "dispatch_failed"):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="follow-up Q&A is available once the research run has completed",
                )
            qa_turn = history.add_turn(
                WorkflowTurn(
                    turn_id=f"turn-{uuid.uuid4().hex[:12]}",
                    workflow_id=workflow_id,
                    seq=history.next_seq(workflow_id),
                    user=user.username,
                    department=user.department,
                    prompt_text=request.prompt,
                    created_at=now,
                    run_id=run_id,
                    status="running",
                )
            )
            trigger_qa(workflow, qa_turn)
            return _detail(workflow, scope_for(user))

        turn = history.add_turn(
            WorkflowTurn(
                turn_id=f"turn-{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                seq=history.next_seq(workflow_id),
                user=user.username,
                department=user.department,
                prompt_text=request.prompt,
                created_at=now,
                run_id=run_id,
            )
        )
        history.set_last_run(workflow_id, run_id, now)
        workflow.last_run_id = run_id
        workflow.updated_at = now
        trigger_run(workflow, turn, auto_approve_plan=request.auto_approve_plan)
        return _detail(workflow, scope_for(user))

    @app.post("/api/v1/workflows/{workflow_id}/approve", response_model=WorkflowDetail)
    def approve_plan(
        workflow_id: str, request: ApprovePlanRequest, user: User = Depends(current_user)
    ) -> WorkflowDetail:
        workflow = _require_workflow(workflow_id, scope_for(user))
        if workflow.status != "awaiting_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="workflow is not awaiting plan approval",
            )
        plan = [
            {"name": i.name, "focus": i.focus, "rationale": i.rationale}
            for i in request.items
            if i.name.strip() and i.focus.strip()
        ]
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="an approved plan needs at least one sub-agent",
            )
        all_turns = history.list_turns(workflow_id)
        turn = all_turns[-1] if all_turns else None
        if turn is None or not turn.run_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="no pending turn to resume for this workflow",
            )
        run_id = turn.run_id
        if workflow.last_run_id != run_id:
            history.set_last_run(workflow_id, run_id, _now())
        now = _now()
        history.update_run_status(run_id, "running", "")
        history.update_turn_status(workflow_id, turn.turn_id, "running")
        history.update_workflow_status(workflow_id, "running", now)
        request_obj = RunRequest(
            run_id=run_id,
            workflow_id=workflow_id,
            turn_id=turn.turn_id,
            department=workflow.department,
            user=turn.user,
            prompt=turn.prompt_text,
            sandbox=Sandbox(pvc_name=workflow.pvc_name, view_path=workflow.sandbox_view_path),
        )
        try:
            nemoclaw.resume_run(request_obj, plan)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "plan approval resume failed for run %s (workflow %s): %s",
                run_id,
                workflow_id,
                exc,
            )
            failed_at = _now()
            history.update_run_status(run_id, "dispatch_failed", failed_at)
            history.update_turn_status(workflow_id, turn.turn_id, "dispatch_failed")
            history.update_workflow_status(workflow_id, "error", failed_at)
        return _detail(_require_workflow(workflow_id, scope_for(user)), scope_for(user))

    @app.get("/api/v1/workflows/{workflow_id}", response_model=WorkflowGraph)
    def get_workflow(workflow_id: str, user: User = Depends(current_user)) -> WorkflowGraph:
        scope = scope_for(user)
        workflow = _require_workflow(workflow_id, scope)
        graph = build_graph(
            workflow_id,
            span_source.fetch(scope),
            run_id=workflow.last_run_id or None,
        )
        if graph.nodes:
            return graph
        return WorkflowGraph(
            workflow_id=workflow.workflow_id,
            department=workflow.department,
            nodes=[],
            edges=[],
        )

    @app.get("/api/v1/workflows/{workflow_id}/detail", response_model=WorkflowDetail)
    def workflow_detail(workflow_id: str, user: User = Depends(current_user)) -> WorkflowDetail:
        workflow = _require_workflow(workflow_id, scope_for(user))
        return _detail(workflow, scope_for(user))

    def _require_workflow(workflow_id: str, department: str | None) -> Workflow:
        try:
            return history.get_workflow(workflow_id, department)
        except WorkflowNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found"
            ) from exc

    @app.delete("/api/v1/workflows/{workflow_id}", response_model=DeleteWorkflowResult)
    def delete_workflow(
        workflow_id: str, user: User = Depends(current_user)
    ) -> DeleteWorkflowResult:
        # Visibility uses the same department scope as every other read (admins: all).
        workflow = _require_workflow(workflow_id, scope_for(user))
        # Ownership: a user may delete only workflows they created; admins may delete any.
        if not user.is_admin and workflow.created_by != user.username:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="you can only delete workflows you created",
            )
        log = logging.getLogger(__name__)
        removed: dict[str, list[str]] = {
            "sandboxes": [],
            "jobs": [],
            "snapshots": [],
            "pvcs": [],
        }
        try:
            removed = sandboxes.delete_workflow_resources(
                workflow.department,
                workflow.workflow_id,
                agent_sandbox=settings.agent_sandbox,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "workflow %s resource teardown failed: %s", workflow.workflow_id, exc
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"could not delete workflow resources: {exc}",
            ) from exc
        # 2) Telemetry spans (the graph/metrics/audit source) then control rows.
        with contextlib.suppress(Exception):
            span_source.delete_workflow(workflow.workflow_id)
        history.delete_workflow(workflow.workflow_id)
        # 3) Audit trail — who deleted what, and what was reaped.
        with contextlib.suppress(Exception):
            history.record_admin_action(
                AdminAction(
                    action_id=f"act-{uuid.uuid4().hex[:12]}",
                    actor=user.username,
                    department=workflow.department,
                    action="delete_workflow",
                    target=workflow.workflow_id,
                    ts=_now(),
                    result=(
                        f"sandboxes={len(removed.get('sandboxes', []))} "
                        f"jobs={len(removed.get('jobs', []))} "
                        f"snapshots={len(removed.get('snapshots', []))} "
                        f"pvcs={len(removed.get('pvcs', []))}"
                    ),
                )
            )
        return DeleteWorkflowResult(
            workflow_id=workflow.workflow_id,
            department=workflow.department,
            deleted=True,
            sandboxes=removed.get("sandboxes", []),
            jobs=removed.get("jobs", []),
            snapshots=removed.get("snapshots", []),
            pvcs=removed.get("pvcs", []),
        )

    def _wake_hint(workflow: Workflow, storage: WorkflowStorageState | None) -> WorkflowWakeHint | None:
        """Hint for the UI when continue will remount/restore retained block volumes."""
        if settings.runtime.mode != "kubernetes" or not settings.sandbox.wake_on_continue:
            return None
        # Same liveness predicate as fan-out wake (any Running pod for this workflow).
        alive = False
        try:
            alive = sandboxes.sandboxes_alive(workflow.department, workflow.workflow_id)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "wake hint liveness for %s failed: %s", workflow.workflow_id, exc
            )
        head_present = False
        retained = 0
        snaps = 0
        last_chk = ""
        if storage:
            retained = len(storage.pvcs)
            snaps = len(storage.snapshots)
            last_chk = storage.last_checkpoint or (
                storage.snapshots[0].name if storage.snapshots else ""
            )
            head_present = any(
                p.role == "head" or p.name == workflow.workflow_id for p in storage.pvcs
            )
        finished = workflow.status in ("completed", "error", "dispatch_failed")
        continue_will_wake = (
            finished
            and not alive
            and (retained > 0 or snaps > 0)
        )
        if continue_will_wake:
            if head_present:
                msg = (
                    "Sandbox pods are gone after keep-alive. Continuing remounts the retained "
                    "head PVC"
                    + (f" and {retained - 1} agent volume(s)" if retained > 1 else "")
                    + (
                        f"; missing volumes restore from the last checkpoint ({last_chk})."
                        if snaps
                        else "."
                    )
                )
            elif snaps:
                msg = (
                    "Sandbox pods and the head PVC are gone. Continuing recreates the same head "
                    f"PVC from checkpoint {last_chk or 'the latest VolumeSnapshot'} — not a new head."
                )
            else:
                msg = (
                    "Sandbox pods are gone. Continuing will reuse this workflow's storage id "
                    "(no new head PVC)."
                )
        elif not alive and finished:
            msg = "Sandbox pods are idle; no retained volumes or checkpoints were found."
        elif alive:
            msg = "Sandbox pods are still live (keep-alive window)."
        else:
            msg = ""
        return WorkflowWakeHint(
            sandboxes_alive=alive,
            continue_will_wake=continue_will_wake,
            head_pvc_present=head_present,
            retained_pvc_count=retained,
            snapshot_count=snaps,
            last_checkpoint=last_chk,
            message=msg,
        )

    def _qa_messages(
        workflow: Workflow, turns: list[WorkflowTurn], spans: list
    ) -> list[QaMessage]:
        seq_by_run = {t.run_id: t.seq for t in turns if t.run_id}
        messages: list[QaMessage] = []
        for span in spans:
            if span.workflow_id != workflow.workflow_id or span.kind != "qa":
                continue
            degraded = span.attributes.get("qa_degraded") == "true"
            if not span.end_time:
                state = "running"
            elif degraded:
                state = "degraded"
            else:
                state = span.status or "completed"
            messages.append(
                QaMessage(
                    turn_id=span.attributes.get("turn_id", ""),
                    run_id=span.run_id,
                    seq=seq_by_run.get(span.run_id, 0),
                    user=span.user,
                    question=span.attributes.get("question", ""),
                    answer=span.attributes.get("answer", ""),
                    citations_json=span.attributes.get("citations_json", ""),
                    status=state,
                    created_at=span.start_time,
                    produced_by=span.attributes.get("produced_by", ""),
                    degraded=degraded,
                )
            )
        messages.sort(key=lambda m: (m.seq, m.created_at))
        return messages

    def _detail(workflow: Workflow, department: str | None) -> WorkflowDetail:
        turns = history.list_turns(workflow.workflow_id)
        spans = span_source.fetch(department)
        graph = build_graph(
            workflow.workflow_id, spans, run_id=workflow.last_run_id or None
        )
        storage = _storage_state(workflow)
        return WorkflowDetail(
            workflow=workflow,
            turns=turns,
            graph=graph,
            storage=storage,
            wake_hint=_wake_hint(workflow, storage),
            qa_messages=_qa_messages(workflow, turns, spans),
        )

    def _storage_state(workflow: Workflow) -> WorkflowStorageState | None:
        if settings.runtime.mode != "kubernetes":
            return None
        try:
            raw = sandboxes.get_storage_state(workflow.department, workflow.workflow_id)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "storage state for %s failed: %s", workflow.workflow_id, exc
            )
            return None
        return WorkflowStorageState(
            workflow_id=raw["workflow_id"],
            department=raw["department"],
            head_pvc=raw["head_pvc"],
            pvcs=[WorkflowPvcInfo(**row) for row in raw.get("pvcs", [])],
            snapshots=[VolumeSnapshotInfo(**row) for row in raw.get("snapshots", [])],
            clone_enabled=raw.get("clone_enabled", True),
            checkpoint_enabled=raw.get("checkpoint_enabled", True),
            checkpoint_on_subagent_complete=raw.get(
                "checkpoint_on_subagent_complete", True
            ),
            wake_on_continue=raw.get(
                "wake_on_continue", settings.sandbox.wake_on_continue
            ),
            snapshot_class=raw.get("snapshot_class", ""),
            last_checkpoint=raw.get("last_checkpoint", ""),
        )

    @app.get(
        "/api/v1/workflows/{workflow_id}/storage",
        response_model=WorkflowStorageState,
    )
    def workflow_storage(
        workflow_id: str, user: User = Depends(current_user)
    ) -> WorkflowStorageState:
        workflow = _require_workflow(workflow_id, scope_for(user))
        state = _storage_state(workflow)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage state unavailable (kubernetes mode required)",
            )
        return state

    @app.post(
        "/api/v1/workflows/{workflow_id}/checkpoint",
        response_model=VolumeSnapshotInfo,
    )
    def checkpoint_workflow(
        workflow_id: str,
        request: CheckpointRequest,
        user: User = Depends(current_user),
    ) -> VolumeSnapshotInfo:
        workflow = _require_workflow(workflow_id, scope_for(user))
        pvc_name = request.pvc_name or workflow.pvc_name or workflow_id
        try:
            snap_name = sandboxes.create_volume_snapshot(
                workflow.department,
                workflow_id,
                pvc_name,
                label=request.label or "manual",
            )
            state = _storage_state(workflow)
            if state:
                for snap in state.snapshots:
                    if snap.name == snap_name:
                        return snap
            return VolumeSnapshotInfo(
                name=snap_name,
                source_pvc=pvc_name,
                ready=True,
                snapshot_class=settings.sandbox.snapshot_class or "",
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("checkpoint %s failed: %s", workflow_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"checkpoint failed: {exc}",
            ) from exc

    @app.post(
        "/api/v1/workflows/{workflow_id}/volumes/restore",
        response_model=WorkflowStorageState,
    )
    def restore_volume_from_snapshot(
        workflow_id: str,
        request: RestoreVolumeRequest,
        user: User = Depends(current_user),
    ) -> WorkflowStorageState:
        workflow = _require_workflow(workflow_id, scope_for(user))
        try:
            sandboxes.restore_pvc_from_snapshot(
                workflow.department,
                workflow_id,
                request.snapshot_name,
                request.pvc_name,
            )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "volume restore %s failed: %s", workflow_id, exc
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"volume restore failed: {exc}",
            ) from exc
        state = _storage_state(workflow)
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage state unavailable after restore",
            )
        return state

    @app.post("/api/v1/workflows/{workflow_id}/reports", response_model=ReportMeta)
    def create_report(workflow_id: str, user: User = Depends(current_user)) -> ReportMeta:
        workflow = _require_workflow(workflow_id, scope_for(user))
        graph = build_graph(
            workflow_id,
            span_source.fetch(scope_for(user)),
            run_id=workflow.last_run_id or None,
        )
        version = history.next_report_version(workflow_id)
        content = _report_content(workflow, graph.nodes, version, user.username)
        pdf = render_report_pdf(content)
        meta = ReportMeta(
            report_id=f"rep-{uuid.uuid4().hex[:12]}",
            workflow_id=workflow_id,
            department=workflow.department,
            version=version,
            title=workflow.title,
            created_by=user.username,
            created_at=content.created_at,
            size=len(pdf),
        )
        return history.save_report(meta, pdf)

    @app.get("/api/v1/workflows/{workflow_id}/reports", response_model=list[ReportMeta])
    def list_reports(workflow_id: str, user: User = Depends(current_user)) -> list[ReportMeta]:
        _require_workflow(workflow_id, scope_for(user))
        return history.list_reports(workflow_id)

    @app.get("/api/v1/workflows/{workflow_id}/reports/{report_id}")
    def download_report(
        workflow_id: str, report_id: str, user: User = Depends(current_user)
    ) -> Response:
        _require_workflow(workflow_id, scope_for(user))
        pdf = history.get_report_pdf(workflow_id, report_id)
        if pdf is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{report_id}.pdf"'},
        )

    @app.get("/api/v1/stats", response_model=AdminStats)
    def department_stats(user: User = Depends(current_user)) -> AdminStats:
        scope = scope_for(user)
        workflows = history.list_workflows(scope)
        return compute_stats(
            workflows,
            _all_turns(workflows),
            span_source.fetch(scope),
            reports=_reports_count(workflows),
        )

    _tools_cache: dict[str, tuple[float, list[ToolInfo]]] = {}

    @app.get("/api/v1/tools", response_model=list[ToolInfo])
    def list_tools(
        department: str | None = None,
        user: User = Depends(current_user),
    ) -> list[ToolInfo]:
        import logging
        import time

        log = logging.getLogger(__name__)
        dept = (department or user.department).strip()
        if not user.is_admin and department and department != user.department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

        configured = [
            ToolInfo(name=name, description="department MCP tool")
            for name in settings.mcp_tools_for(dept)
        ]

        cached = _tools_cache.get(dept)
        if cached and cached[0] > time.time():
            return cached[1]

        live: list[ToolInfo] = []
        if settings.mcp.enabled:
            try:
                live = [
                    ToolInfo(name=t.get("name", ""), description=t.get("description", ""))
                    for t in RagMcpClient(settings.mcp, dept).list_tools()
                    if t.get("name")
                ]
            except Exception as exc:
                log.warning("MCP list_tools probe failed for %s: %s", dept, exc)

        allowed = set(settings.mcp_tools_for(dept))
        if allowed and live:
            live = [t for t in live if t.name in allowed]

        tools = live if live else configured
        if live:
            log.info("MCP tools live for %s: %s", dept, [t.name for t in live])
        elif configured:
            log.info(
                "MCP live probe empty for %s; returning %d configured tool(s)",
                dept,
                len(configured),
            )

        if tools:
            _tools_cache[dept] = (time.time() + 30.0, tools)
        return tools

    @app.get("/api/v1/sandboxes", response_model=list[SandboxStat])
    def sandbox_stats(user: User = Depends(current_user)) -> list[SandboxStat]:
        depts = departments() if user.is_admin else [user.department]
        try:
            return cluster.sandbox_stats(depts)
        except Exception as exc:
            logging.getLogger(__name__).warning("sandbox stats failed: %s", exc)
            return [
                SandboxStat(department=d, namespace=f"vastclaw-{d}", error=str(exc))
                for d in depts
            ]

    @app.get("/api/v1/workflows/{workflow_id}/agents", response_model=list[AgentLiveness])
    def workflow_agents(
        workflow_id: str, user: User = Depends(current_user)
    ) -> list[AgentLiveness]:
        workflow = _require_workflow(workflow_id, scope_for(user))
        try:
            return cluster.agent_liveness(workflow.department, workflow_id)
        except Exception as exc:
            logging.getLogger(__name__).warning("agent liveness failed: %s", exc)
            return []

    @app.websocket("/api/v1/workflows/{workflow_id}/agents/{slug}/shell")
    async def agent_shell(websocket: WebSocket, workflow_id: str, slug: str) -> None:
        token = websocket.query_params.get("token", "")
        try:
            user = tokens.verify(token)
        except InvalidCredentialsError:
            await websocket.close(code=4401)
            return
        try:
            workflow = history.get_workflow(workflow_id, scope_for(user))
        except WorkflowNotFoundError:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        try:
            pod = cluster.find_agent_pod(workflow.department, workflow_id, slug)
        except Exception as exc:
            await websocket.send_text(f"\r\n[shell unavailable: {exc}]\r\n")
            await websocket.close()
            return
        if not pod:
            await websocket.send_text(
                "\r\n[This agent's sandbox has completed — the pod is no longer "
                "running, so there is nothing to attach to. Its artifacts remain "
                "available above.]\r\n"
            )
            await websocket.close()
            return
        try:
            kube = cluster.open_exec(workflow.department, pod)
        except Exception as exc:
            await websocket.send_text(f"\r\n[could not open shell: {exc}]\r\n")
            await websocket.close()
            return
        _audit(user.username, "agent_shell", workflow.department, f"{workflow_id}/{slug}", pod)
        await websocket.send_text(f"\r\n[attached to {pod} · {workflow.department} sandbox]\r\n")
        await _bridge_shell(websocket, kube)

    @app.get("/api/v1/admin/stats", response_model=AdminStats)
    def admin_stats(_: User = Depends(admin_user)) -> AdminStats:
        workflows = history.list_workflows(None)
        return compute_stats(
            workflows,
            _all_turns(workflows),
            span_source.fetch(None),
            reports=_reports_count(workflows),
        )

    @app.get("/api/v1/admin/users", response_model=list[UserStat])
    def admin_users(_: User = Depends(admin_user)) -> list[UserStat]:
        workflows = history.list_workflows(None)
        return compute_users(settings.auth.users, workflows, _all_turns(workflows))

    @app.get("/api/v1/admin/health", response_model=list[DepartmentHealth])
    def admin_health(_: User = Depends(admin_user)) -> list[DepartmentHealth]:
        try:
            return cluster.health(departments())
        except Exception as exc:
            logging.getLogger(__name__).warning("cluster health check failed: %s", exc)
            return [
                DepartmentHealth(department=d, namespace=f"vastclaw-{d}", error=str(exc))
                for d in departments()
            ]

    @app.get("/api/v1/admin/storage", response_model=AdminStorageView)
    def admin_storage(_: User = Depends(admin_user)) -> AdminStorageView:
        """Cross-department agent sandbox storage: PVCs, clones, snapshots per workflow."""
        depts = departments()
        meta = {w.workflow_id: w for w in history.list_workflows(None)}
        try:
            inv = cluster.storage_inventory(depts)
        except Exception as exc:
            logging.getLogger(__name__).warning("admin storage inventory failed: %s", exc)
            inv = [
                DepartmentStorageInventory(
                    department=d,
                    namespace=f"vastclaw-{d}",
                    summary=SandboxStat(department=d, namespace=f"vastclaw-{d}", error=str(exc)),
                )
                for d in depts
            ]
        for dept_inv in inv:
            dept_inv.workflows = [
                row.model_copy(
                    update={
                        "title": (meta[row.workflow_id].title if row.workflow_id in meta else ""),
                        "status": (meta[row.workflow_id].status if row.workflow_id in meta else ""),
                    }
                )
                for row in dept_inv.workflows
            ]
        totals = SandboxStat(department="all", namespace="*")
        classes: set[str] = set()
        for dept_inv in inv:
            s = dept_inv.summary
            totals.pvc_total += s.pvc_total
            totals.pvc_bound += s.pvc_bound
            totals.pvc_pending += s.pvc_pending
            totals.head_pvcs += s.head_pvcs
            totals.subagent_pvcs += s.subagent_pvcs
            totals.cloned_pvcs += s.cloned_pvcs
            totals.restored_pvcs += s.restored_pvcs
            totals.volume_snapshots += s.volume_snapshots
            totals.snapshots_ready += s.snapshots_ready
            totals.capacity_bytes += s.capacity_bytes
            totals.active_agents += s.active_agents
            totals.workflows += s.workflows
            if s.storage_class:
                classes.update(part.strip() for part in s.storage_class.split(",") if part.strip())
        from vastclaw_ui.k8s import _human_bytes

        totals.storage_class = ", ".join(sorted(classes))
        totals.capacity_human = _human_bytes(totals.capacity_bytes)
        return AdminStorageView(departments=inv, totals=totals)

    @app.get("/api/v1/admin/audit", response_model=list[AdminAction])
    def admin_audit(_: User = Depends(admin_user)) -> list[AdminAction]:
        return history.list_admin_actions()

    def _audit(actor: str, action: str, department: str, target: str, result: str) -> None:
        try:
            history.record_admin_action(
                AdminAction(
                    action_id=f"act-{uuid.uuid4().hex[:12]}",
                    actor=actor,
                    department=department,
                    action=action,
                    target=target,
                    ts=_now(),
                    result=result,
                )
            )
        except Exception as exc:  # audit is best-effort, never fail the operation
            logging.getLogger(__name__).warning("admin audit write failed: %s", exc)

    @app.post("/api/v1/admin/departments/{department}/restart", response_model=RestartResult)
    def restart_department(department: str, user: User = Depends(admin_user)) -> RestartResult:
        if department not in departments():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown department")
        try:
            result = cluster.restart_department(department)
        except Exception as exc:
            _audit(
                user.username, "restart_department", department, f"vastclaw-{department}", "error"
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        _audit(
            user.username,
            "restart_department",
            department,
            f"vastclaw-{department}",
            ",".join(result.restarted),
        )
        return result

    @app.post("/api/v1/admin/reset", response_model=ResetResult)
    def reset_platform(user: User = Depends(admin_user)) -> ResetResult:
        deleted: list[str] = []
        for dept in departments():
            try:
                deleted.extend(sandboxes.delete_department_pvcs(dept))
            except Exception as exc:
                logging.getLogger(__name__).warning("reset: pvc delete in %s failed: %s", dept, exc)
        tables: list[str] = []
        try:
            span_source.reset()
            tables.append("spans")
        except Exception as exc:
            logging.getLogger(__name__).warning("reset: spans clear failed: %s", exc)
        try:
            history.reset()
            tables.extend(["workflows", "workflow_turns", "runs"])
        except Exception as exc:
            logging.getLogger(__name__).warning("reset: control tables clear failed: %s", exc)
        _audit(user.username, "reset_platform", "", "all", f"pvcs={len(deleted)}")
        return ResetResult(departments=departments(), pvcs_deleted=deleted, tables_cleared=tables)

    @app.post("/api/v1/admin/restart", response_model=list[RestartResult])
    def restart_platform(user: User = Depends(admin_user)) -> list[RestartResult]:
        results: list[RestartResult] = []
        for dept in departments():
            try:
                result = cluster.restart_department(dept)
            except Exception as exc:
                _audit(user.username, "restart_platform", dept, f"vastclaw-{dept}", "error")
                logging.getLogger(__name__).warning("restart of %s failed: %s", dept, exc)
                results.append(RestartResult(department=dept))
                continue
            _audit(
                user.username,
                "restart_platform",
                dept,
                f"vastclaw-{dept}",
                ",".join(result.restarted),
            )
            results.append(result)
        return results

    return app


def _configure_logging() -> None:
    """Make our own INFO logs visible under uvicorn."""
    import os

    level = os.environ.get("VASTCLAW_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,  # uvicorn may have installed a root handler already
    )
    access = os.environ.get("VASTCLAW_ACCESS_LOG_LEVEL", "").upper()
    if access:
        logging.getLogger("uvicorn.access").setLevel(getattr(logging, access, logging.INFO))


def build_default_app() -> FastAPI:
    _configure_logging()
    settings = Settings.from_yaml()
    source: SpanSource = VastDBSpanSource(settings.vast, settings.vdb)
    history: HistoryStore = VastDBHistoryStore(settings.vast, settings.vdb)
    for store in (history, source):
        try:
            store.ensure_schema()
        except Exception as exc:  # best-effort bootstrap; never crashloop the pod
            logging.getLogger(__name__).error(
                "VastDB schema bootstrap INCOMPLETE — requests touching the missing "
                "table(s) will fail until this is resolved and the pod restarts: %s",
                exc,
            )
    provisioner = KubernetesProvisioner(settings.sandbox)
    nemoclaw: NemoClawClient
    if settings.runtime.mode == "kubernetes":
        nemoclaw = KubernetesRuntime(
            settings,
            VastDBSpanSource(settings.vast, settings.vdb),
            VastDBHistoryStore(settings.vast, settings.vdb),
            provisioner,
        )
    else:
        nemoclaw = LocalResearchRuntime(
            VastDBSpanSource(settings.vast, settings.vdb),
            VastDBHistoryStore(settings.vast, settings.vdb),
            settings,
        )
    # Hybrid: local break-glass admin + VAST VMS auth for department users.
    authenticator: Authenticator = VmsAuthenticator(settings.auth)
    return create_app(
        settings,
        source,
        authenticator=authenticator,
        history_store=history,
        provisioner=provisioner,
        nemoclaw_client=nemoclaw,
    )
