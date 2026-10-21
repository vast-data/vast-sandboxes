"""Per-department workspace seed docs for OpenClaw sandboxes."""

from __future__ import annotations

_TEMPLATES: dict[str, dict[str, str]] = {
    "sales_calls": {
        "agents_intro": (
            "You are a specialist sub-agent in an enterprise earnings-call intelligence "
            "platform running inside an isolated VAST sandbox."
        ),
        "how_to_work": (
            "- Ground every finding in the Earnings-21 earnings-call corpus via the "
            "`vast-rag` MCP tools "
            "(`transcript_search`, `similar_calls`, `list_call_metadata`).\n"
            "- Cite the company (and sector / quarter when relevant) for each point.\n"
            "- Be specific and actionable: call out risks, competitive signals, and "
            "next steps.\n"
            "- Write your report to this workspace so it is captured as an artifact."
        ),
        "tools": (
            "- `vast-rag` MCP server (VastDB vector search over the earnings-call corpus): "
            "`transcript_search`, `similar_calls`, `list_call_metadata`.\n"
            "- A shell scoped to this sandbox for scratch work."
        ),
    },
    "generic": {
        "agents_intro": (
            "You are a specialist sub-agent in a multi-purpose agentic platform running "
            "inside an isolated VAST sandbox."
        ),
        "how_to_work": (
            "- Ground every finding via the department MCP tools registered as `vast-rag`.\n"
            "- Cite your sources for each point and be specific and actionable.\n"
            "- Write your report to this workspace so it is captured as an artifact."
        ),
        "tools": (
            "- `vast-rag` MCP server (department tool surface).\n"
            "- A shell scoped to this sandbox for scratch work."
        ),
    },
}


def render_agents_md(subagent: str, focus: str, workspace_kind: str) -> str:
    tpl = _TEMPLATES.get(workspace_kind, _TEMPLATES["generic"])
    return (
        f"# {subagent}\n\n"
        f"{tpl['agents_intro']}\n\n"
        f"## Your focus\n{focus}\n\n"
        f"## How to work\n{tpl['how_to_work']}\n"
    )


def render_tools_md(workspace_kind: str) -> str:
    tpl = _TEMPLATES.get(workspace_kind, _TEMPLATES["generic"])
    return f"# Available tools\n\n{tpl['tools']}\n"
