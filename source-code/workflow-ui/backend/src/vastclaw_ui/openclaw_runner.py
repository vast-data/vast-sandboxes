"""Thin adapter around the real NVIDIA OpenClaw CLI."""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from vastclaw_ui.config import Settings
from vastclaw_ui.workspace_templates import render_agents_md, render_tools_md

_log = logging.getLogger("vastclaw.openclaw")


class OpenClawError(RuntimeError):
    """Raised when the openclaw CLI is unavailable or a command fails."""


def available() -> bool:
    """True if the openclaw binary is on PATH (i.e. we're on the openclaw image)."""
    return shutil.which("openclaw") is not None


def state_dir(settings: Settings) -> Path:
    """The .openclaw tree — on the mounted sandbox PVC so it persists on VAST."""
    configured = settings.openclaw.state_dir.strip()
    if configured:
        return Path(configured)
    return Path(settings.runtime.workspace_mount) / ".openclaw"


def workspace_dir(settings: Settings) -> Path:
    return state_dir(settings) / "workspace"


def _model_primary(settings: Settings) -> str:
    explicit = settings.openclaw.model_primary.strip()
    if explicit:
        return explicit
    # OpenClaw refs NVIDIA NIM models as "nvidia/<catalog-id>".
    return f"nvidia/{settings.model.model}"


_ANONYMOUS_KEY = "local-nim"


def _provider_key(key: str, settings: Settings) -> str:
    if key:
        return key
    return _ANONYMOUS_KEY if settings.model.allow_anonymous else ""


def _auth_key(settings: Settings) -> str:
    """The key OpenClaw's onboarding/auth flow sees."""
    return _provider_key(
        settings.model.api_key or settings.model.effective_embed_api_key, settings
    )


def render_config(settings: Settings) -> Path:
    """Write $state_dir/openclaw.json from Settings. Returns the state dir."""
    sd = state_dir(settings)
    ws = workspace_dir(settings)
    ws.mkdir(parents=True, exist_ok=True)

    timeout_seconds = max(int(settings.model.request_timeout_seconds or 0), 180)
    provider = {
        "baseUrl": settings.model.base_url,
        "api": "openai-completions",
        "apiKey": _provider_key(settings.model.api_key, settings),
        "timeoutSeconds": timeout_seconds,
    }
    embed_provider = {
        "baseUrl": settings.model.effective_embed_base_url,
        "api": "openai-completions",
        "apiKey": _provider_key(settings.model.effective_embed_api_key, settings),
        "timeoutSeconds": timeout_seconds,
    }
    # 2026.7: provider keys on env.
    config: dict[str, Any] = {
        "env": {
            "NVIDIA_API_KEY": _auth_key(settings),
            "OPENAI_API_KEY": _provider_key(
                settings.model.effective_embed_api_key, settings
            ),
        },
        "models": {
            "providers": {
                "nvidia": dict(provider),
                "openai": dict(embed_provider),
            }
        },
        "tools": {"profile": settings.openclaw.tool_profile},
        "agents": {
            "defaults": {
                "model": {"primary": _model_primary(settings)},
                "workspace": str(ws),
                "subagents": {"maxSpawnDepth": 2},
            }
        },
    }

    sd.mkdir(parents=True, exist_ok=True)
    (sd / "openclaw.json").write_text(json.dumps(config, indent=2))
    return sd


def write_workspace_docs(settings: Settings, department: str, subagent: str, focus: str) -> None:
    """Seed the sandbox workspace with the AGENTS/TOOLS/MEMORY context OpenClaw reads."""
    ws = workspace_dir(settings)
    ws.mkdir(parents=True, exist_ok=True)
    kind = settings.department_config(department).workspace_kind
    (ws / "AGENTS.md").write_text(render_agents_md(subagent, focus, kind))
    (ws / "TOOLS.md").write_text(render_tools_md(kind))
    mem = ws / "MEMORY.md"
    if not mem.exists():
        mem.write_text("# MEMORY\n\n_(persists across runs on this sandbox's VAST PVC)_\n")


def ensure_ready(settings: Settings) -> None:
    """Prepare state so `openclaw agent --local` can run headlessly."""
    if not available():
        raise OpenClawError("openclaw binary not found on PATH")
    render_config(settings)
    env = _env(settings)
    marker = state_dir(settings) / ".config-hash"
    if marker.exists():
        return
    try:
        subprocess.run(
            [
                "openclaw", "onboard",
                "--non-interactive", "--accept-risk",
                "--mode", "local",
                "--auth-choice", "apiKey",
                "--skip-skills",
            ],
            env=env, capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # tolerate: config already valid
        _log.warning("openclaw onboard skipped: %s", exc)
    marker.write_text("1")


def _env(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env["NVIDIA_API_KEY"] = _auth_key(settings)
    env["OPENAI_API_KEY"] = _provider_key(settings.model.effective_embed_api_key, settings)
    env["OPENCLAW_STATE_DIR"] = str(state_dir(settings))
    return env


def run_agent(
    settings: Settings,
    agent_id: str,
    message: str,
    *,
    validator: Callable[[str], None] | None = None,
    retry_hint: str = "",
) -> tuple[str, dict[str, Any]]:
    attempts = max(1, int(settings.openclaw.max_attempts))
    last_exc: OpenClawError | None = None
    for attempt in range(1, attempts + 1):
        body = message if attempt == 1 or not retry_hint else f"{message}\n\n{retry_hint}"
        try:
            return _run_agent_once(settings, agent_id, body, validator=validator)
        except OpenClawError as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            backoff = min(30.0, 2.0 ** attempt) + random.uniform(0, attempt * 1.5)
            _log.warning(
                "openclaw attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, attempts, exc, backoff,
            )
            time.sleep(backoff)
    raise last_exc or OpenClawError("openclaw agent failed")


def _run_agent_once(
    settings: Settings,
    agent_id: str,
    message: str,
    *,
    validator: Callable[[str], None] | None = None,
) -> tuple[str, dict[str, Any]]:
    """One real OpenClaw agent invocation. Raises OpenClawError on any failure."""
    if not available():
        raise OpenClawError("openclaw binary not found on PATH")
    timeout = settings.openclaw.timeout_seconds
    cmd = [
        "openclaw", "agent",
        "--agent", agent_id,
        "--message", message,
        "--local",
        "--json",
        "--timeout", str(int(timeout)),
    ]
    try:
        proc = subprocess.run(
            cmd, env=_env(settings), capture_output=True, text=True,
            timeout=timeout + 60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenClawError(f"openclaw agent invocation failed: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.strip()
        _log.warning("openclaw agent exit %s stderr:\n%s", proc.returncode, err)
        raise OpenClawError(
            f"openclaw agent exited {proc.returncode}: {err[-1500:]}"
        )
    raw = _parse_envelope(proc.stdout)
    text = _reply_text(raw)
    if not text:
        _log.warning(
            "openclaw returned no reply text; stdout=%s stderr_tail=%s",
            proc.stdout.strip()[:1500], proc.stderr.strip()[-500:],
        )
        raise OpenClawError(
            "openclaw agent returned no reply text; raw envelope keys="
            f"{sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__}"
        )
    if validator is not None:
        validator(text)
    return text, raw


def _parse_envelope(stdout: str) -> dict[str, Any]:
    """--json reserves stdout for the JSON envelope. Be tolerant of a leading banner
    by scanning for the first balanced JSON object."""
    stdout = stdout.strip()
    try:
        return json.loads(stdout)
    except (ValueError, TypeError):
        pass
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(stdout[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _content_from_message(msg: Any) -> str:
    """Extract text from a message object whose `content` is a string or a list of
    {type,text} parts (OpenAI / Anthropic style)."""
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        joined = "\n".join(p for p in parts if p).strip()
        if joined:
            return joined
    return ""


def _reply_text(raw: dict[str, Any]) -> str:
    """Pull the assistant reply out of the OpenClaw JSON envelope, tolerating the many
    shapes the CLI / provider have used."""
    if not isinstance(raw, dict):
        return ""
    # 1. Flat string fields.
    for key in ("reply", "text", "output", "output_text", "response", "content", "result"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # 2. message.content (string or parts).
    for key in ("message", "result", "data"):
        got = _content_from_message(raw.get(key))
        if got:
            return got
    # 3. OpenAI-style choices[].message.content / choices[].text.
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            got = _content_from_message(first.get("message")) or _content_from_message(first)
            if got:
                return got
            txt = first.get("text")
            if isinstance(txt, str) and txt.strip():
                return txt.strip()
    # 4. messages[] transcript — take the last assistant message.
    msgs = raw.get("messages")
    if isinstance(msgs, list):
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") in (None, "assistant"):
                got = _content_from_message(m)
                if got:
                    return got
    # 5. Deep fallback: the longest string found under any content/text/reply key.
    best = ""
    stack: list[Any] = [raw]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k in ("text", "content", "reply", "message") and len(v.strip()) > len(best):
                    best = v.strip()
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return best
