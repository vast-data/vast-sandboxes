from __future__ import annotations

from vastclaw_ui.models import GraphEdge, GraphNode, WorkflowGraph, WorkflowSummary
from vastclaw_ui.store import Span


def _root_agent(spans: list[Span]) -> str:
    ids = {s.span_id for s in spans}
    roots = [s for s in spans if not s.parent_span_id or s.parent_span_id not in ids]
    roots.sort(key=lambda s: s.start_time)
    return roots[0].agent_name if roots else spans[0].agent_name


def _status(spans: list[Span]) -> str:
    if any(s.status == "error" for s in spans):
        return "error"
    if any(not s.end_time for s in spans):
        return "running"
    return "ok"


# Agent names that are the orchestrator itself, not a sandboxed sub-agent.
_LEAD_AGENTS = frozenset({"research-lead", "synthesis"})


def summarize(spans: list[Span]) -> list[WorkflowSummary]:
    by_workflow: dict[str, list[Span]] = {}
    for span in spans:
        by_workflow.setdefault(span.workflow_id, []).append(span)

    summaries: list[WorkflowSummary] = []
    for workflow_id, group in by_workflow.items():
        group.sort(key=lambda s: s.start_time)
        summaries.append(
            WorkflowSummary(
                workflow_id=workflow_id,
                department=group[0].department,
                root_agent=_root_agent(group),
                user=group[0].user,
                span_count=len(group),
                agent_count=len({s.agent_name for s in group}),
                subagent_count=len(
                    {s.agent_name for s in group if s.agent_name not in _LEAD_AGENTS}
                ),
                started_at=group[0].start_time,
                status=_status(group),
            )
        )
    summaries.sort(key=lambda s: s.started_at, reverse=True)
    return summaries


def build_graph(
    workflow_id: str,
    spans: list[Span],
    *,
    run_id: str | None = None,
) -> WorkflowGraph:
    """Build the DAG for a workflow."""
    group = [s for s in spans if s.workflow_id == workflow_id]
    if run_id:
        scoped = [s for s in group if s.run_id == run_id]
        if scoped or not group:
            group = scoped
    if not group:
        return WorkflowGraph(workflow_id=workflow_id, department="", nodes=[], edges=[])

    ids = {s.span_id for s in group}
    nodes = [
        GraphNode(
            id=s.span_id,
            label=s.name,
            agent_name=s.agent_name,
            kind=s.kind,
            status=s.status,
            start_time=s.start_time,
            end_time=s.end_time,
            attributes=s.attributes,
        )
        for s in group
    ]
    edges = [
        GraphEdge(source=s.parent_span_id, target=s.span_id)
        for s in group
        if s.parent_span_id and s.parent_span_id in ids
    ]
    return WorkflowGraph(
        workflow_id=workflow_id,
        department=group[0].department,
        nodes=nodes,
        edges=edges,
    )
