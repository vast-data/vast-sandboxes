"""Department-pack prompts + DAG span vocabulary — no domain logic in runtime."""

from __future__ import annotations

from vastclaw_ui.config import DepartmentSettings, Settings

_GENERIC_LEAD_INSTRUCTIONS = (
    "As the research lead, decompose the goal into 2-4 focused sub-agent tasks, "
    "each investigating one angle through your role's lens. Respond ONLY with a JSON "
    "array of objects with keys 'name' (short kebab-case), 'focus' (one sentence) and "
    "'rationale' (one sentence on why this sub-agent matters). No prose."
)

_TRANSCRIPT_LEAD_INSTRUCTIONS = (
    "As the research lead, decompose the goal into 2-4 focused sub-agent tasks, each "
    "investigating one angle through your role's lens. When the goal targets a specific "
    "company or sector, make the sub-agent focus name it explicitly so retrieval can "
    "filter on it. Respond ONLY with a JSON array of objects with keys 'name' (short "
    "kebab-case), 'focus' (one sentence) and 'rationale' (one sentence on why this "
    "sub-agent matters to the goal). No prose."
)

_GENERIC_SYNTHESIS_TRANSCRIPT = (
    "Merge the sub-agent findings into an ACTIONABLE briefing written from your role's "
    "perspective. The findings are grounded in public earnings-call transcripts and tagged "
    "with company / sector / quarter. Respond ONLY with a JSON object with these "
    "keys:\n"
    "  'summary': a well-structured MARKDOWN report using this outline "
    "(include every heading exactly once, in this order):\n"
    "    # <Short report title derived from the goal>\n"
    "    ## Executive summary\n"
    "    2-4 sentences: what matters most and what to do next.\n"
    "    ## Per-company next steps\n"
    "    One bullet per named company: "
    "'**<Company>** (<sector>): <single most important specific action>'.\n"
    "    ## Cross-cutting themes\n"
    "    2-4 ### subheadings, each with 1-2 sentences or short bullets "
    "(de-duplicated patterns only).\n"
    "    ## Evidence by workstream\n"
    "    One ### subheading per sub-agent source; 2-4 bullets citing "
    "companies/sectors from that workstream.\n"
    "  'customer_actions': array of {'customer': named company, 'stage': their "
    "sector, 'action': the single most important specific next step for that "
    "company, 'source': the sub-agent the evidence came from}. One object per "
    "named company actually present in the findings — never invent one.\n"
    "  'highlights': array of {'insight': short string, 'source': the sub-agent "
    "name it came from} — 3-6 attributed insights.\n"
    "  'recommendation': ONE crisp paragraph — the overall recommended play across "
    "the portfolio, phrased as concrete actions an account team can take now.\n"
    "Use clean markdown (headings, bullets, **bold** for company names). "
    "Be specific; do not pad. Return valid JSON only."
)

_GENERIC_SYNTHESIS_FINANCE = (
    "Merge the sub-agent findings into an ACTIONABLE commercial briefing from a finance "
    "lens (guidance, margin, pricing power, cost pressure, capital allocation, revenue "
    "recognition). Respond "
    "ONLY with a JSON object with these keys:\n"
    "  'summary': markdown with # title, ## Executive summary, ## Guidance & economics, "
    "## Margin & capital risks, ## Evidence by workstream.\n"
    "  'customer_actions': array of {'customer', 'stage', 'action', 'source'} focused on "
    "commercial / finance next steps (customer = company, stage = sector).\n"
    "  'highlights': array of {'insight', 'source'} (3-6 items).\n"
    "  'recommendation': one crisp paragraph for finance/revops.\n"
    "Return valid JSON only."
)


def _display(dept: DepartmentSettings, department: str) -> str:
    return (dept.display_name or department or "Department").strip() or "Department"


def span_vocab(settings: Settings, department: str) -> dict[str, str]:
    """Short, department-specific labels for DAG span names and attributes."""
    dept = settings.department_config(department)
    display = _display(dept, department)
    evidence = (dept.evidence_type or "transcript").lower()
    tools = list(dept.mcp_tool_filter)
    primary = tools[0] if tools else "transcript_search"

    if department.strip().lower() == "finance":
        search_title = "Search earnings"
        corpus = "earnings-call corpus (finance lens)"
        role = f"{display} analyst"
        synthesize_log = "Merged sub-agent findings into the finance briefing."
        synthesize_decision = f"Synthesized the {display} commercial analysis report."
        lead_log = f"Initialized {display} research lead in the department sandbox."
        plan_decision_suffix = "finance workstreams"
        findings_label = dept.synthesis.findings_label.strip() or (
            "Sub-agent findings (finance lens, grounded in the earnings-call corpus)"
        )
    else:
        search_title = "Search earnings"
        corpus = "earnings-call corpus"
        role = f"{display} analyst"
        synthesize_log = "Merged the most relevant sub-agent findings into the report."
        synthesize_decision = f"Synthesized the {display} earnings-call intelligence report."
        lead_log = f"Initialized {display} research lead in the department sandbox."
        plan_decision_suffix = "workstreams"
        findings_label = dept.synthesis.findings_label.strip() or (
            "Sub-agent findings (grounded in the earnings-call corpus)"
        )

    return {
        "department": department,
        "department_display": display,
        "evidence_type": evidence,
        "grounding_mode": dept.grounding_mode or "",
        "workspace_kind": dept.workspace_kind or "",
        "role_title": role,
        "lead_title": f"{display} lead",
        "plan_title": "Plan",
        "await_title": "Await approval",
        "approved_title": "Plan approved",
        "synthesize_title": "Synthesize",
        "search_title": search_title,
        "checkpoint_head_title": "Checkpoint head",
        "checkpoint_workspace_title": "Checkpoint workspace",
        "primary_search_tool": primary,
        "mcp_tools": ",".join(tools),
        "corpus_label": corpus,
        "findings_label": findings_label,
        "lead_log": lead_log,
        "synthesize_log": synthesize_log,
        "synthesize_decision": synthesize_decision,
        "plan_decision_suffix": plan_decision_suffix,
        "wake_title": "Wake volumes",
    }


def department_span_attrs(settings: Settings, department: str) -> dict[str, str]:
    """Attributes stamped on every span so the UI can show role/tools per department."""
    v = span_vocab(settings, department)
    return {
        "department": v["department"],
        "department_display": v["department_display"],
        "evidence_type": v["evidence_type"],
        "grounding_mode": v["grounding_mode"],
        "workspace_kind": v["workspace_kind"],
        "role_title": v["role_title"],
        "lead_title": v["lead_title"],
        "primary_search_tool": v["primary_search_tool"],
        "mcp_tools": v["mcp_tools"],
        "corpus_label": v["corpus_label"],
    }


def planning_lead_prompt(settings: Settings, department: str) -> str:
    dept = settings.department_config(department)
    if dept.planning.lead_instructions.strip():
        instructions = dept.planning.lead_instructions.strip()
    else:
        instructions = _TRANSCRIPT_LEAD_INSTRUCTIONS
    persona = settings.persona(department).strip()
    return f"{persona}\n\n{instructions}" if persona else instructions


def planning_user_goal(
    settings: Settings, department: str, prompt: str, corpus_context: str
) -> str:
    dept = settings.department_config(department)
    preamble = dept.planning.context_preamble.strip()
    if not corpus_context:
        return prompt
    if preamble:
        return f"{prompt}\n\n{preamble}\n{corpus_context}"
    return (
        f"{prompt}\n\nBusiness context available in the earnings-call corpus (prefer "
        f"sub-agents scoped to a specific company, sector or quarter when "
        f"the goal implies it):\n{corpus_context}"
    )


def synthesis_system_prompt(settings: Settings, department: str) -> str:
    dept = settings.department_config(department)
    custom = dept.synthesis.system_instructions.strip()
    if custom:
        base = custom
    elif department.strip().lower() == "finance":
        base = _GENERIC_SYNTHESIS_FINANCE
    else:
        base = _GENERIC_SYNTHESIS_TRANSCRIPT
    persona = settings.persona(department).strip()
    return f"{persona}\n\n{base}" if persona else base


def synthesis_user_prompt(
    settings: Settings, department: str, prompt: str, contributions_text: str
) -> str:
    dept = settings.department_config(department)
    label = dept.synthesis.findings_label.strip() or span_vocab(settings, department)[
        "findings_label"
    ]
    return f"Goal:\n{prompt}\n\n{label}:\n{contributions_text}"


def agent_output_contract(settings: Settings, department: str) -> str:
    """JSON output contract for OpenClaw sub-agents."""
    if department.strip().lower() == "finance":
        return (
            "Given an overall goal and your specific focus, respond with ONLY a single "
            "JSON object (no prose, no markdown fences) with these keys:\n"
            '  "summary": a concise markdown findings write-up (<220 words) using:\n'
            "    ## Findings\n"
            "    2-4 short paragraphs or bullets with **bold** company/sector names; "
            "emphasize guidance, margin, pricing power, capital allocation.\n"
            "    ## Implications\n"
            "    1-3 bullets on commercial / finance risk for the overall goal.\n"
            '  "key_findings": array of 3-6 short factual bullet strings,\n'
            '  "metrics": array of 3-6 objects {"label": str, "value": number, "unit": str},\n'
            '  "sources": array of short strings naming the kind of data you relied on,\n'
            '  "considerations": array of 2-4 short strings,\n'
            '  "confidence": a number between 0 and 1.\n'
            "Return valid JSON only."
        )
    return (
        "Given an overall goal and your specific focus, respond with ONLY a single "
        "JSON object (no prose, no markdown fences) with these keys:\n"
        '  "summary": a concise markdown findings write-up (<220 words) using this structure:\n'
        "    ## Findings\n"
        "    2-4 short paragraphs or bullets with **bold** company/sector names where relevant.\n"
        "    ## Implications\n"
        "    1-3 bullets on what this means for the overall goal.\n"
        '  "key_findings": array of 3-6 short factual bullet strings,\n'
        '  "metrics": array of 3-6 objects {"label": str, "value": number, "unit": str} '
        "that are chartable quantitative figures for this focus (best-estimate if exact "
        "numbers are unknown),\n"
        '  "sources": array of short strings naming the kind of data you relied on,\n'
        '  "considerations": array of 2-4 short strings describing the trade-offs / '
        "decision considerations you weighed,\n"
        '  "confidence": a number between 0 and 1 for how confident you are.\n'
        "Return valid JSON only."
    )
