#!/usr/bin/env python3
"""Deploy preflight helpers (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import socket
import ssl
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_FAILS = 0


def _ok(msg: str) -> None:
    print(f"✓ {msg}")


def _fail(msg: str) -> None:
    global _FAILS
    _FAILS += 1
    print(f"✗ {msg}")


def _warn(msg: str) -> None:
    print(f"⚠ {msg}")


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw[:1] in "\"'" and raw[-1:] == raw[:1]:
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _extract_top_map(text: str, name: str) -> dict[str, Any]:
    lines = text.splitlines()
    start = -1
    for i, line in enumerate(lines):
        if line.startswith(f"{name}:") and not line.startswith(" "):
            start = i
            break
    if start < 0:
        return {}

    out: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(0, out)]
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, rest = stripped.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest.startswith("#"):
            rest = ""
        if " #" in rest and not (rest.startswith('"') or rest.startswith("'")):
            rest = rest.split(" #", 1)[0].rstrip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if rest in ("", "|", ">", ">-", "|-", "|+") or rest.startswith(">"):
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(rest)
    return out


def _require(section: dict[str, Any], key: str, label: str) -> str:
    val = str(section.get(key) or "").strip()
    if not val:
        _fail(f"{label} missing in config")
    elif "CHANGE_ME" in val:
        _fail(f"{label} is still a placeholder")
    return val


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sigv4_headers(
    *,
    method: str,
    url: str,
    access_key: str,
    secret_key: str,
    region: str,
    payload: bytes,
) -> dict[str, str]:
    parsed = urlparse(url)
    host = parsed.netloc
    canonical_uri = parsed.path or "/"
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        [method, canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _hmac(
        _hmac(_hmac(_hmac(("AWS4" + secret_key).encode("utf-8"), datestamp), region), "s3"),
        "aws4_request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def _s3_request(
    method: str,
    url: str,
    *,
    access_key: str,
    secret_key: str,
    ssl_verify: bool,
    body: bytes = b"",
) -> tuple[int, bytes]:
    headers = _sigv4_headers(
        method=method,
        url=url,
        access_key=access_key,
        secret_key=secret_key,
        region="us-east-1",
        payload=body,
    )
    if body:
        headers["Content-Length"] = str(len(body))
        headers["Content-Type"] = "application/octet-stream"
    req = Request(url, data=body if body else None, method=method, headers=headers)
    ctx = None
    if urlparse(url).scheme == "https":
        ctx = ssl.create_default_context()
        if not ssl_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    try:
        with urlopen(req, timeout=30, context=ctx) as resp:
            return resp.getcode() or 200, resp.read()
    except HTTPError as exc:
        return exc.code, exc.read() if exc.fp else b""


def _tcp_ok(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port(url_or_host: str, default_port: int) -> tuple[str, int]:
    raw = url_or_host.strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    port = parsed.port or default_port
    return host, port


def _head_bucket(
    endpoint: str,
    bucket: str,
    *,
    access_key: str,
    secret_key: str,
    ssl_verify: bool,
) -> int:
    code, _ = _s3_request(
        "HEAD",
        f"{endpoint.rstrip('/')}/{bucket}",
        access_key=access_key,
        secret_key=secret_key,
        ssl_verify=ssl_verify,
    )
    return code


def _probe_rw(
    endpoint: str,
    bucket: str,
    *,
    access_key: str,
    secret_key: str,
    ssl_verify: bool,
) -> None:
    probe_key = f".vastclaw-deploy-probe/{uuid.uuid4().hex}"
    probe_url = f"{endpoint.rstrip('/')}/{bucket}/{probe_key}"
    body = b"vast-claw deploy probe\n"
    common = dict(access_key=access_key, secret_key=secret_key, ssl_verify=ssl_verify)
    try:
        code, _ = _s3_request("PUT", probe_url, body=body, **common)
        if code not in (200, 201):
            _fail(f"s3://{bucket}/ write failed (HTTP {code}) — grant PutObject on the bucket")
            return
        code, got = _s3_request("GET", probe_url, **common)
        if code != 200 or got != body:
            _fail(f"s3://{bucket}/ read-after-write failed (HTTP {code})")
            return
        _ok(f"s3://{bucket}/ read/write OK via {endpoint}")
    except URLError as exc:
        _fail(f"s3://{bucket}/ probe failed: {exc}")
    finally:
        try:
            _s3_request("DELETE", probe_url, **common)
        except Exception:
            pass


def _load(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text()
    return {
        "vast": _extract_top_map(text, "vast"),
        "s3": _extract_top_map(text, "s3"),
        "vdb": _extract_top_map(text, "vdb"),
        "sandbox": _extract_top_map(text, "sandbox"),
        "runtime": _extract_top_map(text, "runtime"),
        "auth": _extract_top_map(text, "auth"),
        "model": _extract_top_map(text, "model"),
    }


def run_config(path: Path) -> int:
    """Local: required keys only. No TCP / S3 (laptop may not reach data VIPs)."""
    cfg = _load(path)
    vast, s3, vdb = cfg["vast"], cfg["s3"], cfg["vdb"]
    sandbox, runtime, auth, model = cfg["sandbox"], cfg["runtime"], cfg["auth"], cfg["model"]

    _require(vast, "access_key", "vast.access_key")
    _require(vast, "secret_key", "vast.secret_key")
    _require(s3, "endpoint", "s3.endpoint")
    _require(vdb, "endpoint", "vdb.endpoint")
    _require(vdb, "telemetry_bucket", "vdb.telemetry_bucket")
    _require(vdb, "control_bucket", "vdb.control_bucket")
    storage_class = _require(sandbox, "storage_class", "sandbox.storage_class")
    runtime_mode = str(runtime.get("mode") or "local").strip()
    agent_image = str(runtime.get("agent_image") or "").strip()
    jwt = str(auth.get("jwt_secret") or "").strip()

    if jwt and "CHANGE_ME" in jwt:
        _fail("auth.jwt_secret is still a placeholder")
    elif jwt:
        _ok("auth.jwt_secret set")

    if runtime_mode == "kubernetes" and not agent_image:
        _fail("runtime.agent_image required when runtime.mode=kubernetes")
    elif agent_image:
        _ok(f"runtime.agent_image={agent_image}")

    if storage_class:
        _ok(f"sandbox.storage_class={storage_class}")

    model_enabled = bool(model.get("enabled", True))
    allow_anon = bool(model.get("allow_anonymous", False))
    api_key = str(model.get("api_key") or "").strip()
    if model_enabled and not allow_anon and not api_key:
        _fail("model.enabled but model.api_key empty (set key or allow_anonymous: true)")
    elif model_enabled:
        _ok("model credentials configured")

    _ok("config keys present (data-plane probes run in-cluster)")

    print(f"PREFLIGHT_STORAGE_CLASS={storage_class}")
    print(f"PREFLIGHT_RUNTIME_MODE={runtime_mode}")
    print(f"PREFLIGHT_AGENT_IMAGE={agent_image}")

    if _FAILS:
        print(f"\n✗ config preflight: {_FAILS} check(s) failed")
        return 1
    print("✓ config preflight OK")
    return 0


def run_data_plane(path: Path) -> int:
    """In-cluster: reachability + bucket probes from a pod that can see data VIPs."""
    cfg = _load(path)
    vast, s3, vdb, auth = cfg["vast"], cfg["s3"], cfg["vdb"], cfg["auth"]

    access_key = _require(vast, "access_key", "vast.access_key")
    secret_key = _require(vast, "secret_key", "vast.secret_key")
    s3_endpoint = _require(s3, "endpoint", "s3.endpoint")
    vdb_endpoint = _require(vdb, "endpoint", "vdb.endpoint")
    telemetry_bucket = _require(vdb, "telemetry_bucket", "vdb.telemetry_bucket")
    control_bucket = _require(vdb, "control_bucket", "vdb.control_bucket")
    vms_host = str(auth.get("vms_host") or "").strip()
    vms_port = int(auth.get("vms_port") or 443)
    s3_ssl = bool(s3.get("ssl_verify", False))

    if _FAILS:
        return 1

    vdb_host, vdb_port = _host_port(
        vdb_endpoint, 80 if vdb_endpoint.startswith("http://") else 443
    )
    if vdb_host and _tcp_ok(vdb_host, vdb_port):
        _ok(f"vdb.endpoint reachable from cluster ({vdb_host}:{vdb_port})")
    else:
        _fail(f"vdb.endpoint not reachable from cluster ({vdb_endpoint})")

    s3_host, s3_port = _host_port(s3_endpoint, 80 if s3_endpoint.startswith("http://") else 443)
    if s3_host and _tcp_ok(s3_host, s3_port):
        _ok(f"s3.endpoint reachable from cluster ({s3_host}:{s3_port})")
    else:
        _fail(f"s3.endpoint not reachable from cluster ({s3_endpoint})")

    if vms_host:
        if _tcp_ok(vms_host, vms_port):
            _ok(f"auth.vms_host reachable from cluster ({vms_host}:{vms_port})")
        else:
            _warn(f"auth.vms_host not reachable from cluster ({vms_host}:{vms_port})")

    creds = dict(access_key=access_key, secret_key=secret_key, ssl_verify=s3_ssl)

    for label, bucket in (
        ("vdb.telemetry_bucket", telemetry_bucket),
        ("vdb.control_bucket", control_bucket),
    ):
        try:
            code = _head_bucket(s3_endpoint, bucket, **creds)
        except URLError as exc:
            _warn(f"{label}={bucket} S3 HEAD failed ({exc})")
            continue
        if code in (200, 204):
            _ok(f"{label}={bucket} accessible via s3.endpoint")
        else:
            _warn(
                f"{label}={bucket} not S3-HEAD-able (HTTP {code}) — OK if DATABASE-only for VastDB"
            )

    if _FAILS:
        print(f"\n✗ in-cluster data-plane preflight: {_FAILS} check(s) failed")
        return 1
    print("✓ in-cluster data-plane preflight OK")
    return 0


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("config", "data-plane"):
        print(
            f"usage: {sys.argv[0]} config|data-plane <workflow-ui.config.yaml>",
            file=sys.stderr,
        )
        sys.exit(2)
    mode, cfg = sys.argv[1], Path(sys.argv[2])
    if not cfg.is_file():
        _fail(f"config not found: {cfg}")
        sys.exit(1)
    if mode == "config":
        sys.exit(run_config(cfg))
    sys.exit(run_data_plane(cfg))


if __name__ == "__main__":
    main()
