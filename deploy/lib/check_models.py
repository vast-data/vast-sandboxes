#!/usr/bin/env python3
"""Confirm the configured chat and embed models are being served."""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

from yaml_subset import extract_top_map

_TIMEOUT = 15.0


def _fail(msg: str) -> None:
    print(f"✗ {msg}", flush=True)
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"✓ {msg}", flush=True)


def _listed_models(base_url: str, api_key: str) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl._create_unverified_context() if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        if api_key:
            detail = detail.replace(api_key, "***")
        _fail(f"{url} returned HTTP {exc.code}: {detail}")
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        _fail(f"{url} unreachable: {exc}")
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        _fail(f"{url} did not return a model list")
    names = []
    for row in rows:
        if isinstance(row, dict) and row.get("id"):
            names.append(str(row["id"]))
    return names


def _check(label: str, base_url: str, model_id: str, api_key: str) -> None:
    if not base_url or "CHANGE_ME" in base_url:
        _fail(f"model.{label} endpoint is unset")
    if not model_id or "CHANGE_ME" in model_id:
        _fail(f"model.{label} model id is unset")
    names = _listed_models(base_url, api_key)
    if model_id not in names:
        shown = ", ".join(names[:8]) or "(none)"
        _fail(f"{label} model {model_id} is not served by {base_url} (got {shown})")
    _ok(f"{label} model {model_id} at {base_url}")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <workflow-ui.config.yaml>", file=sys.stderr)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    if not path.is_file():
        _fail(f"config not found: {path}")
    model = extract_top_map(path.read_text(), "model")
    if model.get("enabled") is False:
        _ok("model.enabled is false — model check skipped")
        return
    chat_key = str(model.get("api_key") or "").strip()
    embed_key = str(model.get("embed_api_key") or chat_key).strip()
    embed_url = str(model.get("embed_base_url") or model.get("base_url") or "").strip()
    _check("chat", str(model.get("base_url") or "").strip(), str(model.get("model") or "").strip(), chat_key)
    _check(
        "embed",
        embed_url,
        str(model.get("embed_model") or "").strip(),
        embed_key,
    )


if __name__ == "__main__":
    main()
