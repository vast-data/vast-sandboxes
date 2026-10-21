
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vastclaw_ui import openclaw_runner
from vastclaw_ui.config import Settings
from vastclaw_ui.department_prompts import (
    agent_output_contract,
    department_span_attrs,
    span_vocab,
)
from vastclaw_ui.llm import NimEmbedder
from vastclaw_ui.openclaw_runner import OpenClawError
from vastclaw_ui.rag_client import RagMcpClient
from vastclaw_ui.store import Span, VastDBSpanSource

_log = logging.getLogger("vastclaw.agent_runner")

_DEFAULT_PERSONA = (
    "You are a specialist earnings-call intelligence analyst who grounds every finding "
    "in real public company earnings transcripts."
)


_TOOLCALL_MARKER = "<TOOLCALL>"

CONTRACT_RETRY_HINT = (
    "IMPORTANT: your previous reply was rejected. Do NOT emit a tool call and do NOT "
    "emit <TOOLCALL>. You already have every snippet you need in the message above — no "
    "retrieval is required. Reply with ONLY the single JSON object described above, "
    'starting with { and containing "summary" and "key_findings".'
)


def _validate_contract(text: str) -> None:
    """Reject replies that are not the agreed findings contract."""
    from vastclaw_ui.openclaw_runner import OpenClawError

    if _TOOLCALL_MARKER in text:
        raise OpenClawError(
            "model returned a text tool call instead of findings "
            f"(reply starts: {text.strip()[:120]!r})"
        )
    obj = _parse_structured(text)
    if not obj.get("summary") and not obj.get("key_findings"):
        raise OpenClawError(
            "reply does not satisfy the findings contract (no summary/key_findings; "
            f"keys={sorted(obj)[:8]})"
        )


def _parse_structured(text: str) -> dict[str, Any]:
    try:
        start = text.find("{")
        end = text.rfind("}")
        obj = json.loads(text[start : end + 1], strict=False) if start >= 0 else {}
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _clean_metrics(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict) or "label" not in item or "value" not in item:
            continue
        try:
            value = float(item["value"])
        except (ValueError, TypeError):
            continue
        out.append(
            {"label": str(item["label"])[:48], "value": value, "unit": str(item.get("unit", ""))}
        )
    return out[:6]


def _str_list(raw: Any, limit: int = 8) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x)[:200] for x in raw if str(x).strip()][:limit]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_TEXT_KINDS = {"md", "txt", "json", "csv", "log", "yaml", "yml", "py", "sql", "tsv"}
_MAX_INLINE_BYTES = 64 * 1024


def _collect_artifacts(workspace: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        files = sorted(p for p in workspace.rglob("*") if p.is_file())
    except OSError as exc:
        _log.warning("artifact scan failed: %s", exc)
        return out
    for p in files:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        kind = p.suffix.lstrip(".").lower() or "bin"
        try:
            rel = str(p.relative_to(workspace))
        except ValueError:
            rel = p.name
        entry: dict[str, Any] = {
            "name": p.name,
            "rel": rel,
            "path": str(p),
            "bytes": size,
            "kind": kind,
        }
        if kind in _TEXT_KINDS and 0 < size <= _MAX_INLINE_BYTES:
            try:
                entry["content"] = p.read_text(errors="replace")
            except OSError:
                pass
        out.append(entry)
    return out


def _match_facet(query: str, metadata: dict[str, Any], key: str) -> str | None:
    best: str | None = None
    for facet in metadata.get(key, []) or []:
        val = str(facet.get("value") or "")
        if len(val) >= 3 and val.lower() in query and (best is None or len(val) > len(best)):
            best = val
    return best


def _derive_filters(query_text: str, metadata: dict[str, Any]) -> dict[str, str]:
    q = query_text.lower()
    filters: dict[str, str] = {}

    for facet_key, arg in (
        ("calls", "engagement_id"),
        ("customers", "customer"),
        ("opportunities", "opportunity"),
        ("sales_stages", "sales_stage"),
    ):
        match = _match_facet(q, metadata, facet_key)
        if match:
            filters[arg] = match

    speakers = {str(f.get("value") or "").lower() for f in metadata.get("speaker_types", []) or []}
    wants_external = any(
        w in q
        for w in (
            "analyst asked",
            "analyst question",
            "analyst said",
            "buyer",
            "customer objection",
            "customer said",
            "prospect",
            "customer sentiment",
        )
    )
    wants_company = any(
        w in q
        for w in (
            "management said",
            "ceo said",
            "cfo said",
            "company said",
            "our rep",
            "sales rep",
            "the rep",
            "salesperson",
            "our team said",
        )
    )
    # Speaker tags: company / analyst / operator (plus customer when present).
    if wants_external and not wants_company:
        if "analyst" in speakers:
            filters["speaker_type"] = "analyst"
        elif "customer" in speakers:
            filters["speaker_type"] = "customer"
    elif wants_company and not wants_external:
        if "company" in speakers:
            filters["speaker_type"] = "company"
        elif "rep" in speakers:
            filters["speaker_type"] = "rep"

    return filters


def _evidence_from_transcript_hit(h: dict[str, Any]) -> dict[str, Any]:
    dist = h.get("distance")
    return {
        "evidence_type": "transcript",
        "engagement_id": str(h.get("engagement_id", "")),
        "customer": str(h.get("customer", "")),
        "opportunity": str(h.get("opportunity", "")),
        "sales_stage": str(h.get("sales_stage", "")),
        "speaker_type": str(h.get("speaker_type", "")),
        "speaker_name": str(h.get("speaker_name", "")),
        "call_date": str(h.get("call_date", "")),
        "distance": round(float(dist), 4) if isinstance(dist, int | float) else None,
        "text": str(h.get("text", "")).strip(),
        "table": str(h.get("table") or "").strip(),
    }


def _rag_query_text(settings: Settings, department: str, prompt: str, focus: str) -> str:
    """Build the text sent to transcript RAG for pre-fetch grounding."""
    prompt = (prompt or "").strip()
    focus = (focus or "").strip()

    # Transcript RAG: the sub-agent focus leads; the overall goal is light context.
    if not prompt:
        return focus
    return f"{focus}\n\nContext for this call analysis: {prompt}"


def _ground_subagent(
    settings: Settings,
    department: str,
    query_text: str,
    client: RagMcpClient,
) -> tuple[str, list[dict[str, Any]], str, int, dict[str, str]]:
    """Pre-fetch grounding snippets. Returns (grounding_text, evidence, rag_status, embed_tokens, filters)."""
    evidence: list[dict[str, Any]] = []
    filters: dict[str, str] = {}
    embed_tokens = 0

    embedder = NimEmbedder(settings.model)
    if not embedder.enabled:
        return "", [], "skipped", embed_tokens, filters

    metadata = client.list_call_metadata(limit=200)
    filters = _derive_filters(query_text, metadata)
    embed_tokens = _est_tokens(query_text)
    qvec = embedder.embed_query(query_text)
    rag_hits = client.transcript_search(qvec, settings.mcp.rag_top_k, **filters)
    if not rag_hits:
        return "", [], "empty", embed_tokens, filters

    evidence = [_evidence_from_transcript_hit(h) for h in rag_hits]
    grounding = (
        "Relevant transcript snippets retrieved from the earnings-call corpus "
        "(each tagged with its company / sector / quarter):\n"
        + "\n".join(
            f"- [{e['customer'] or e['engagement_id']}"
            f"{(' · ' + e['sales_stage']) if e['sales_stage'] else ''} · {e['speaker_type']}] "
            f"{e['text']}"
            for e in evidence
        )
    )
    return grounding, evidence, "ok", embed_tokens, filters


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    run_id = _env("RUN_ID")
    workflow_id = _env("WORKFLOW_ID")
    department = _env("DEPARTMENT")
    user = _env("USER_NAME", "unknown")
    subagent = _env("SUBAGENT")
    focus = _env("FOCUS")
    prompt = _env("PROMPT")
    persona = _env("DEPARTMENT_PERSONA").strip() or _DEFAULT_PERSONA
    parent = _env("PARENT_SPAN_ID")
    job_name = _env("JOB_NAME", subagent)
    subagent_slug = _env("SUBAGENT_SLUG", subagent)
    pvc_name = _env("PVC_NAME", "")
    storage_class = _env("STORAGE_CLASS", "")
    clone_source_pvc = _env("CLONE_SOURCE_PVC", "")
    access_mode = _env("ACCESS_MODE", "")

    settings = Settings.from_yaml()
    spans = VastDBSpanSource(settings.vast, settings.vdb)
    try:
        spans.ensure_schema()
    except Exception as exc:  # schema is normally already created by the backend
        _log.warning("span schema ensure skipped: %s", exc)

    sub_id = f"{run_id}-{subagent}"
    started = _now()

    tool = f"openclaw:{settings.model.model}"
    key_findings: list[str] = []
    metrics: list[dict[str, Any]] = []
    sources: list[str] = []
    considerations: list[str] = []
    confidence = ""

    # --- RAG grounding: department-specific query for MCP retrieval ---
    query_text = _rag_query_text(settings, department, prompt, focus)
    grounding = ""
    evidence: list[dict[str, Any]] = []
    rag_status = "skipped"
    embed_tokens = 0
    filters: dict[str, str] = {}
    try:
        if settings.mcp.enabled:
            client = RagMcpClient(settings.mcp, department)
            grounding, evidence, rag_status, embed_tokens, filters = _ground_subagent(
                settings, department, query_text, client
            )
        else:
            rag_status = "skipped"
    except Exception as exc:  # embeddings or MCP hiccup — proceed ungrounded
        _log.warning("RAG grounding failed: %s", exc)
        rag_status = "error"

    vocab = span_vocab(settings, department)
    dept_attrs = department_span_attrs(settings, department)
    user_prompt = f"Overall goal:\n{prompt}\n\nYour focus:\n{focus}\n\n"
    if grounding:
        user_prompt += (
            grounding + "\n\nGround your findings ONLY in the snippets above. Cite the "
            "company (and sector / quarter where relevant) for each point, and be "
            "specific and actionable — call out risks, next steps and stage-appropriate moves.\n\n"
        )
    user_prompt += "Produce your findings."

    findings = ""
    ptok = ctok = 0
    status = "ok"
    engine_used = "openclaw"
    contract = agent_output_contract(settings, department)
    system_prompt = f"{persona}\n\n{contract}"

    try:
        openclaw_runner.ensure_ready(settings)
        openclaw_runner.write_workspace_docs(settings, department, subagent, focus)
        reply, _raw = openclaw_runner.run_agent(
            settings,
            settings.openclaw.agent_id,
            f"{system_prompt}\n\n{user_prompt}",
            validator=_validate_contract,
            retry_hint=CONTRACT_RETRY_HINT,
        )
        obj = _parse_structured(reply)
        findings = str(obj.get("summary") or reply)
        key_findings = _str_list(obj.get("key_findings"))
        metrics = _clean_metrics(obj.get("metrics"))
        sources = _str_list(obj.get("sources"))
        considerations = _str_list(obj.get("considerations"), limit=4)
        if isinstance(obj.get("confidence"), int | float):
            confidence = f"{float(obj['confidence']):.2f}"
        ptok, ctok = _est_tokens(user_prompt), _est_tokens(reply)
        tool = f"openclaw:{settings.model.model}"
        engine_used = "openclaw"
        status = "ok"
    except OpenClawError as exc:
        _log.warning("openclaw agent failed (%s); marking sub-agent degraded", exc)
        findings = f"_(OpenClaw failed: {exc})_\n\n**{subagent}** focus: {focus}."
        considerations = [f"OpenClaw failed; scoped to: {focus}"]
        ptok, ctok = _est_tokens(user_prompt), _est_tokens(findings)
        tool = "openclaw-failed"
        engine_used = "openclaw-failed"
        status = "degraded"

    structured = {
        "key_findings_json": json.dumps(key_findings),
        "metrics_json": json.dumps(metrics),
        "sources_json": json.dumps(sources),
        "considerations_json": json.dumps(considerations),
        "confidence": confidence,
    }

    # Persist the artifact to this sub-agent's own VAST PVC (best-effort).
    artifact_path = ""
    try:
        workspace = Path(settings.runtime.workspace_mount)
        workspace.mkdir(parents=True, exist_ok=True)
        target = workspace / f"{subagent}.md"
        bullets = "".join(f"- {k}\n" for k in key_findings)
        target.write_text(
            f"# {subagent}\n\n_Focus: {focus}_\n\n{findings}\n\n"
            + (f"## Key findings\n{bullets}\n" if bullets else "")
            + (f"```json\n{json.dumps(metrics, indent=2)}\n```\n" if metrics else "")
        )
        artifact_path = str(target)
    except Exception as exc:
        _log.warning("could not write artifact to PVC: %s", exc)

    # Real artifact manifest: every file the agent left on its own VAST sandbox PVC.
    artifacts = _collect_artifacts(Path(settings.runtime.workspace_mount))

    ended = _now()
    duration_ms = int(
        (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds() * 1000
    )
    common = {
        "department": department,
        "user": user,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "trace_id": run_id,
    }
    primary_tool = vocab["primary_search_tool"]
    rag_tool_detail = f"{primary_tool} (MCP → VastDB)"
    agent_span = Span(
        **common,
        span_id=sub_id,
        parent_span_id=parent,
        name=subagent,
        kind="agent",
        agent_name=subagent,
        start_time=started,
        end_time=ended,
        status=status,
        attributes={
            **dept_attrs,
            "phase": "research",
            "input": focus,
            "decision": f"Investigate ({vocab['role_title']}): {focus}",
            "data_produced": findings,
            "log": (
                f"{vocab['department_display']} sub-agent {subagent} ran in pod "
                f"{job_name} on VAST PVC {pvc_name}"
                + (
                    f" (zero-copy clone of {clone_source_pvc})."
                    if clone_source_pvc
                    else "."
                )
                + f" Tools: {vocab['mcp_tools'] or primary_tool}."
            ),
            "job_name": job_name,
            "pod": _env("HOSTNAME", ""),
            "engine": engine_used,
            "engine_endpoint": settings.model.base_url,
            "tool": tool,
            "artifact_path": artifact_path,
            "tokens_prompt": str(ptok),
            "tokens_completion": str(ctok),
            "duration_ms": str(duration_ms),
            # Real artifacts + the sandbox that produced them (surfaced in the UI).
            "artifacts_json": json.dumps(artifacts),
            "slug": subagent_slug,
            "pvc_name": pvc_name,
            "storage_class": storage_class,
            "clone_source_pvc": clone_source_pvc,
            "access_mode": access_mode,
            "mount_path": settings.runtime.workspace_mount,
            "evidence_json": json.dumps(evidence),
            "retrieval_status": rag_status,
            **structured,
        },
    )
    rag_span = Span(
        **common,
        span_id=f"{sub_id}-rag",
        parent_span_id=sub_id,
        name=vocab["search_title"],
        kind="tool",
        agent_name=subagent,
        start_time=started,
        end_time=ended,
        status="ok" if rag_status in ("ok", "empty", "skipped") else "degraded",
        attributes={
            **dept_attrs,
            "phase": "retrieval",
            "tool": rag_tool_detail,
            "input": query_text,
            "output": grounding or f"(no snippets: {rag_status})",
            "log": f"Retrieved grounding from {vocab['corpus_label']} via {primary_tool}.",
            "evidence_json": json.dumps(evidence),
            "hits": str(len(evidence)),
            "top_k": str(settings.mcp.rag_top_k),
            "retrieval_status": rag_status,
            "tokens_embed": str(embed_tokens),
            "embed_model": settings.model.embed_model,
            "embed_endpoint": settings.model.effective_embed_base_url,
            "filters_json": json.dumps(filters),
        },
    )
    spans.write_spans([agent_span, rag_span])
    _log.info(
        "sub-agent %s complete (status=%s, tokens=%d/%d, rag=%s hits=%d)",
        subagent, status, ptok, ctok, rag_status, len(evidence),
    )

    keep_alive = max(0, int(getattr(settings.agent_sandbox, "keep_alive_seconds", 0)))
    if keep_alive:
        stop_file = Path(settings.runtime.workspace_mount) / ".openclaw" / "stop"
        _log.info("sub-agent %s live for shell for up to %ds (touch %s to end early)",
                  subagent, keep_alive, stop_file)
        deadline = datetime.now(UTC).timestamp() + keep_alive
        while datetime.now(UTC).timestamp() < deadline:
            if stop_file.exists():
                _log.info("stop sentinel found; ending live window")
                break
            import time as _time
            _time.sleep(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
