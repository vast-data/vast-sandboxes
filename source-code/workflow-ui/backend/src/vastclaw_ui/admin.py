from __future__ import annotations

from vastclaw_ui.config import UserRecord
from vastclaw_ui.models import (
    ActivityItem,
    AdminStats,
    AgentStat,
    DepartmentStat,
    NameCount,
    UserStat,
    Workflow,
    WorkflowTurn,
)
from vastclaw_ui.store import Span


def _span_tokens(span: Span) -> int:
    try:
        p = int(span.attributes.get("tokens_prompt", "0"))
        c = int(span.attributes.get("tokens_completion", "0"))
    except ValueError:
        return 0
    return p + c


def compute_stats(
    workflows: list[Workflow],
    turns: list[WorkflowTurn],
    spans: list[Span],
    recent_limit: int = 12,
    top_users_limit: int = 8,
    top_agents_limit: int = 8,
    activity_days: int = 14,
    reports: int = 0,
) -> AdminStats:
    stats = AdminStats(workflows=len(workflows), reports=reports)
    dept: dict[str, DepartmentStat] = {}

    def d(name: str) -> DepartmentStat:
        return dept.setdefault(name, DepartmentStat(department=name))

    for wf in workflows:
        row = d(wf.department)
        row.workflows += 1
        if wf.status == "running":
            stats.running += 1
            row.running += 1
        elif wf.status == "completed":
            stats.completed += 1
            row.completed += 1
        elif wf.status == "error":
            stats.error += 1
            row.error += 1

    agents: set[str] = set()
    dept_agents: dict[str, set[str]] = {}
    runs: set[str] = set()
    agent_stats: dict[str, AgentStat] = {}
    tools: dict[str, int] = {}
    for span in spans:
        tokens = _span_tokens(span)
        stats.tokens_used += tokens
        d(span.department).tokens += tokens
        agents.add(f"{span.department}:{span.agent_name}")
        dept_agents.setdefault(span.department, set()).add(span.agent_name)

        key = f"{span.department}:{span.agent_name}"
        agent = agent_stats.setdefault(
            key, AgentStat(name=span.agent_name, department=span.department)
        )
        agent.tokens += tokens
        agent.spans += 1

        if span.kind == "tool":
            stats.tool_calls += 1
            d(span.department).tool_calls += 1
            agent.tool_calls += 1
            tool_name = span.attributes.get("tool") or span.name or span.agent_name
            tools[tool_name] = tools.get(tool_name, 0) + 1
        if span.run_id:
            runs.add(span.run_id)
    stats.agents_served = len(agents)
    stats.runs = len(runs)
    for name, names in dept_agents.items():
        d(name).agents = len(names)

    stats.avg_agents_per_workflow = (
        round(stats.agents_served / stats.workflows, 1) if stats.workflows else 0.0
    )
    stats.top_agents = sorted(
        agent_stats.values(), key=lambda a: (a.tokens, a.spans), reverse=True
    )[:top_agents_limit]
    stats.tool_breakdown = sorted(
        (NameCount(name=n, count=c) for n, c in tools.items()),
        key=lambda t: t.count,
        reverse=True,
    )

    stats.by_department = sorted(dept.values(), key=lambda r: r.workflows, reverse=True)

    per_day: dict[str, int] = {}
    for wf in workflows:
        day = (wf.created_at or "")[:10]
        if day:
            per_day[day] = per_day.get(day, 0) + 1
    stats.activity_by_day = [
        NameCount(name=day, count=per_day[day]) for day in sorted(per_day)[-activity_days:]
    ]

    # Recent activity from the newest turns (audit: who ran what).
    wf_by_id = {w.workflow_id: w for w in workflows}
    ordered_turns = sorted(turns, key=lambda t: t.created_at, reverse=True)
    for turn in ordered_turns[:recent_limit]:
        wf = wf_by_id.get(turn.workflow_id)
        stats.recent.append(
            ActivityItem(
                workflow_id=turn.workflow_id,
                title=wf.title if wf else turn.workflow_id,
                user=turn.user,
                department=turn.department,
                status=turn.status,
                at=turn.created_at,
            )
        )

    per_user: dict[str, UserStat] = {}
    for wf in workflows:
        u = per_user.setdefault(
            wf.created_by, UserStat(username=wf.created_by, department=wf.department)
        )
        u.workflows += 1
    for turn in ordered_turns:
        if turn.user in per_user and not per_user[turn.user].last_active:
            per_user[turn.user].last_active = turn.created_at
    stats.top_users = sorted(per_user.values(), key=lambda u: u.workflows, reverse=True)[
        :top_users_limit
    ]
    return stats


def compute_users(
    user_records: dict[str, UserRecord],
    workflows: list[Workflow],
    turns: list[WorkflowTurn],
) -> list[UserStat]:
    wf_count: dict[str, int] = {}
    for wf in workflows:
        wf_count[wf.created_by] = wf_count.get(wf.created_by, 0) + 1
    last_active: dict[str, str] = {}
    for turn in sorted(turns, key=lambda t: t.created_at, reverse=True):
        last_active.setdefault(turn.user, turn.created_at)

    out: list[UserStat] = []
    for username, record in user_records.items():
        out.append(
            UserStat(
                username=username,
                department=record.department,
                is_admin=record.is_admin,
                workflows=wf_count.get(username, 0),
                last_active=last_active.get(username, ""),
            )
        )
    return sorted(out, key=lambda u: (u.is_admin, -u.workflows, u.username))
