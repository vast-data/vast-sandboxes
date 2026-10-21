#!/usr/bin/env python3
"""Create the VAST objects this deploy needs. Idempotent. Stdlib only."""

from __future__ import annotations

import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from yaml_subset import extract_top_map, redact_platform_password, set_block_scalar

_PLACEHOLDERS = ("CHANGE_ME", "REPLACE_ME")


def _fail(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"✓ {msg}")


def _placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or any(mark in text for mark in _PLACEHOLDERS)


def _load(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text()
    return {
        "vast": extract_top_map(text, "vast"),
        "vdb": extract_top_map(text, "vdb"),
        "s3": extract_top_map(text, "s3"),
        "auth": extract_top_map(text, "auth"),
        "model": extract_top_map(text, "model"),
        "platform": extract_top_map(text, "platform"),
        "departments": extract_top_map(text, "departments"),
    }


def _csi_creds(config_path: Path) -> dict[str, str]:
    csi = config_path.parent.parent / "csi" / "vast-block-csi.config.yaml"
    if not csi.is_file():
        return {}
    text = csi.read_text()
    vms = extract_top_map(text, "vms")
    storage = extract_top_map(text, "storageclass")
    return {
        "endpoint": str(vms.get("endpoint") or "").strip(),
        "username": str(vms.get("username") or "").strip(),
        "password": str(vms.get("password") or ""),
        "tenant": str(storage.get("tenant_name") or "").strip(),
    }


class Vms:
    def __init__(self, host: str, tenant: str, username: str, password: str) -> None:
        host = host.strip()
        if host.startswith("https://"):
            host = host[len("https://") :]
        elif host.startswith("http://"):
            host = host[len("http://") :]
        host = host.split("/")[0]
        self.host = host
        self.tenant = tenant
        self.username = username
        self.password = password
        self._token = ""
        self._ctx = ssl._create_unverified_context()

    def _url(self, path: str) -> str:
        return f"https://{self.host}{path}"

    def _request(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.tenant:
            headers["X-Tenant-Name"] = self.tenant
        if auth:
            if not self._token:
                self.login()
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(self._url(path), data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60, context=self._ctx) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            if self.password:
                detail = detail.replace(self.password, "***")
            raise RuntimeError(f"VMS {method} {path} -> HTTP {exc.code}: {detail}") from None
        except OSError as exc:
            raise RuntimeError(f"VMS {self.host} unreachable: {exc}") from None

    def login(self) -> None:
        payload = {"username": self.username, "password": self.password}
        url_path = f"/api/token/{self.tenant}/" if self.tenant else "/api/token/"
        try:
            body = self._request("POST", url_path, payload, auth=False)
        except RuntimeError:
            body = self._request("POST", "/api/token/", payload, auth=False)
        token = (body or {}).get("access") or (body or {}).get("token")
        if not token:
            _fail("VMS login succeeded but returned no token")
        self._token = token

    def rows(self, path: str) -> list[dict]:
        collected: list[dict] = []
        seen: set[str] = set()
        while path and path not in seen:
            seen.add(path)
            data = self._request("GET", path)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            if not isinstance(data, dict):
                break
            results = data.get("results")
            if isinstance(results, list):
                collected.extend(item for item in results if isinstance(item, dict))
            nxt = data.get("next")
            if not isinstance(nxt, str) or not nxt:
                break
            path = _api_path(nxt)
        return collected

    def post(self, path: str, body: dict) -> dict:
        data = self._request("POST", path, body)
        return data if isinstance(data, dict) else {}

    def patch(self, path: str, body: dict) -> dict:
        data = self._request("PATCH", path, body)
        return data if isinstance(data, dict) else {}

    def delete(self, path: str, body: dict | None = None) -> bool:
        try:
            self._request("DELETE", path, body)
            return True
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                return False
            raise


def _api_path(url: str) -> str:
    if url.startswith("/"):
        return url
    parsed = urllib.parse.urlparse(url)
    if not parsed.path:
        return ""
    return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path


def _view_bucket(view: dict) -> str:
    bucket = str(view.get("bucket") or "").strip()
    if bucket:
        return bucket
    path = str(view.get("path") or "").strip().strip("/")
    return path.rsplit("/", 1)[-1]


def _absent(exc: RuntimeError) -> bool:
    text = str(exc)
    return "HTTP 404" in text or "does not exist" in text


def _delete_db(vms: Vms, path: str, body: dict) -> bool:
    """DELETE a VastDB table or schema. A 404 means it is already gone."""
    if vms.delete(path, body):
        return True
    latest = "/api/latest/" + path.split("/api/", 1)[-1]
    if latest != path and vms.delete(latest, body):
        return True
    return False


def _empty_database(vms: Vms, bucket: str) -> None:
    """Drop every VastDB table and schema in one bucket."""
    try:
        schemas = vms.rows(f"/api/schemas/?database_name={urllib.parse.quote(bucket, safe='')}&page_size=500")
    except RuntimeError as exc:
        if _absent(exc):
            return
        raise
    names: list[str] = []
    for row in schemas:
        if str(row.get("database_name") or "") not in ("", bucket):
            continue
        name = str(row.get("name") or "").strip()
        if name:
            names.append(name)
    for name in sorted(set(names), key=lambda item: item.count("/"), reverse=True):
        try:
            tables = vms.rows(
                "/api/tables/?database_name="
                f"{urllib.parse.quote(bucket, safe='')}"
                f"&schema_name={urllib.parse.quote(name, safe='')}&page_size=500"
            )
        except RuntimeError as exc:
            if _absent(exc):
                tables = []
            else:
                raise
        for table in tables:
            if str(table.get("database_name") or "") not in ("", bucket):
                continue
            if str(table.get("schema_name") or "") not in ("", name):
                continue
            table_name = str(table.get("name") or "").strip()
            if not table_name:
                continue
            if _delete_db(
                vms,
                "/api/tables/delete/",
                {"database_name": bucket, "schema_name": name, "name": table_name},
            ):
                _ok(f"table {bucket}/{name}.{table_name} removed")
        if _delete_db(vms, "/api/schemas/delete/", {"database_name": bucket, "name": name}):
            _ok(f"schema {bucket}/{name} removed")


def _buckets(cfg: dict[str, dict[str, Any]]) -> list[str]:
    platform, vdb = cfg["platform"], cfg["vdb"]
    names: list[str] = []
    database = str(platform.get("database_bucket") or vdb.get("telemetry_bucket") or "").strip()
    control = str(vdb.get("control_bucket") or database).strip()
    corpus = str(platform.get("corpus_bucket") or "earnings-calls").strip()
    for name in (database, control, corpus):
        if name and name not in names:
            names.append(name)
    return names


def _login_material(path: Path, cfg: dict[str, dict[str, Any]]) -> tuple[str, str, str, str]:
    """Return host, username, password, login tenant. Never prints the password."""
    platform = cfg["platform"]
    auth = cfg["auth"]
    username = str(platform.get("vms_username") or "").strip()
    password = str(platform.get("vms_password") or "")
    tenant = str(auth.get("tenant_name") or "").strip()
    host = str(auth.get("vms_host") or "").strip()
    csi = _csi_creds(path)
    used_csi = False
    if _placeholder(username) or _placeholder(password):
        if _placeholder(csi.get("username")) or _placeholder(csi.get("password")):
            _fail(
                "platform.vms_username / vms_password are empty and "
                "deploy/csi/vast-block-csi.config.yaml has no VMS login"
            )
        username = csi["username"]
        password = csi["password"]
        used_csi = True
    if _placeholder(host):
        host = csi.get("endpoint") or ""
    if _placeholder(host):
        _fail("auth.vms_host is unset and the CSI config has no vms.endpoint")
    login_tenant = csi.get("tenant") if used_csi and csi.get("tenant") else tenant
    if not login_tenant or _placeholder(login_tenant):
        _fail("auth.tenant_name is unset")
    if tenant and login_tenant and tenant != login_tenant:
        _fail(
            f"auth.tenant_name is {tenant} but the VMS login tenant is {login_tenant}. "
            f"Set auth.tenant_name: {login_tenant}"
        )
    return host, username, password, login_tenant


def _ensure_tenant(vms: Vms, name: str) -> dict:
    for row in vms.rows("/api/tenants/?page_size=200"):
        if row.get("name") == name:
            _ok(f"tenant {name}")
            return row
    try:
        created = vms.post("/api/tenants/", {"name": name})
    except RuntimeError as exc:
        _fail(f"tenant {name} does not exist and could not be created: {exc}")
    _ok(f"tenant {name} created")
    return created


def _ensure_s3_user(vms: Vms, name: str) -> dict:
    for row in vms.rows("/api/users/?page_size=500"):
        if row.get("name") == name:
            _ok(f"S3 user {name}")
            return row
    created = vms.post(
        "/api/users/",
        {"name": name, "allow_create_bucket": True, "allow_delete_bucket": True},
    )
    _ok(f"S3 user {name} created")
    return created


def _ensure_user_group(vms: Vms, user: dict, group_name: str) -> None:
    """platform.s3_group. Membership is the user's gids list."""
    if group_name in (user.get("groups") or []):
        _ok(f"S3 user {user.get('name')} is in {group_name}")
        return
    gid = None
    for group in vms.rows("/api/groups/?page_size=500"):
        if group.get("name") == group_name:
            gid = group.get("gid")
            break
    if gid is None:
        _fail(f"local group {group_name} not found")
    gids = [int(item) for item in (user.get("gids") or [])]
    if int(gid) not in gids:
        gids.append(int(gid))
    updated = vms.patch(f"/api/users/{int(user['id'])}/", {"gids": gids})
    groups = updated.get("groups") or []
    if group_name not in groups:
        _fail(f"S3 user {user.get('name')} was not added to {group_name}")
    _ok(f"S3 user {user.get('name')} joined {group_name}")


def access_file(config: Path) -> Path:
    return config.parent / "vast-access.yaml"


def resolved_keys(config: Path) -> tuple[str, str]:
    """Keys from the declaration file, or from vast-access.yaml when those are placeholders."""
    vast = extract_top_map(config.read_text(), "vast")
    access = str(vast.get("access_key") or "")
    secret = str(vast.get("secret_key") or "")
    if not _placeholder(access) and not _placeholder(secret):
        return access, secret
    side = access_file(config)
    if side.is_file():
        stored = extract_top_map(side.read_text(), "vast")
        access = str(stored.get("access_key") or "")
        secret = str(stored.get("secret_key") or "")
    return access, secret


def _keys_ready(config: Path) -> bool:
    access, secret = resolved_keys(config)
    return not _placeholder(access) and not _placeholder(secret)


def _ensure_keys(vms: Vms, user_id: int, path: Path) -> None:
    if _keys_ready(path):
        _ok("vast access key already set — left unchanged")
        return
    created = vms.post(f"/api/users/{user_id}/access_keys/", {})
    access = str(created.get("access_key") or "")
    secret = str(created.get("secret_key") or "")
    if not access or not secret:
        _fail("VMS did not return an access key pair")
    side = access_file(path)
    side.write_text(
        "vast:\n"
        f"  access_key: {json.dumps(access)}\n"
        f"  secret_key: {json.dumps(secret)}\n"
    )
    _ok(f"stored a new vast access key in {side.name}")


def _ensure_managers(vms: Vms, cfg: dict[str, dict[str, Any]], tenant_id: int) -> None:
    users = cfg["auth"].get("users") or {}
    if not isinstance(users, dict):
        return
    needed = []
    for name, record in users.items():
        if not isinstance(record, dict):
            continue
        if record.get("local") in (True, "true", "True"):
            continue
        if _placeholder(name):
            _fail(f"auth.users has a placeholder name ({name})")
        needed.append((str(name), record))
    if not needed:
        _ok("no VMS login users to create")
        return
    role_name = str(cfg["platform"].get("manager_role") or "").strip()
    if _placeholder(role_name):
        _fail("platform.manager_role is required (the VAST role new UI logins join)")
    role_id = None
    for role in vms.rows("/api/roles/?page_size=200"):
        if role.get("name") == role_name:
            role_id = role.get("id")
            break
    if role_id is None:
        _fail(f"VMS role {role_name} not found")
    existing = {row.get("username"): row for row in vms.rows("/api/managers/?page_size=500")}
    for name, record in needed:
        if name in existing:
            _ok(f"VMS login {name}")
            continue
        password = str(record.get("password") or "")
        if _placeholder(password):
            _fail(f"auth.users.{name}.password is required to create that VMS login")
        vms.post(
            "/api/managers/",
            {
                "username": name,
                "password": password,
                "roles": [role_id],
                "user_type": "TENANT_ADMIN",
                "tenant_id": tenant_id,
            },
        )
        _ok(f"VMS login {name} created")


def _ensure_views(vms: Vms, cfg: dict[str, dict[str, Any]], tenant_id: int, owner: str) -> None:
    policy_name = str(cfg["platform"].get("view_policy") or "").strip()
    if _placeholder(policy_name):
        _fail("platform.view_policy is required (an existing S3_NATIVE view policy)")
    policy_id = None
    for policy in vms.rows("/api/viewpolicies/?page_size=200"):
        if policy.get("name") == policy_name:
            policy_id = policy.get("id")
            flavor = str(policy.get("flavor") or "")
            if flavor and flavor != "S3_NATIVE":
                _fail(f"view policy {policy_name} is {flavor}; DATABASE buckets need S3_NATIVE")
            break
    if policy_id is None:
        _fail(f"view policy {policy_name} not found")
    existing = {row.get("bucket"): row for row in vms.rows("/api/views/?page_size=500")}
    for bucket in _buckets(cfg):
        if _placeholder(bucket):
            _fail("a bucket name is still a placeholder")
        found = existing.get(bucket)
        if found:
            protocols = found.get("protocols") or []
            if "DATABASE" not in protocols:
                _fail(f"view for bucket {bucket} exists but has no DATABASE protocol ({protocols})")
            _ok(f"bucket {bucket}")
            continue
        vms.post(
            "/api/views/",
            {
                "path": f"/{bucket}",
                "bucket": bucket,
                "bucket_owner": owner,
                "policy_id": policy_id,
                "protocols": ["S3", "DATABASE"],
                "create_dir": True,
                "tenant_id": tenant_id,
            },
        )
        _ok(f"bucket {bucket} created")


def _require_declared(cfg: dict[str, dict[str, Any]]) -> None:
    """Fail before any VMS write if the config does not name the objects to create."""
    if _placeholder(cfg["platform"].get("view_policy")):
        _fail("platform.view_policy is required (an existing S3_NATIVE view policy)")
    if _placeholder(cfg["platform"].get("s3_user")):
        _fail("platform.s3_user is unset")
    if _placeholder(cfg["platform"].get("s3_group")):
        _fail("platform.s3_group is required")
    users = cfg["auth"].get("users") or {}
    needs_role = False
    if isinstance(users, dict):
        for name, record in users.items():
            if not isinstance(record, dict):
                continue
            if record.get("local") in (True, "true", "True"):
                continue
            if _placeholder(name):
                _fail(f"auth.users has a placeholder name ({name})")
            needs_role = True
            if _placeholder(record.get("password")):
                _fail(f"auth.users.{name}.password is required to create that VMS login")
    if needs_role and _placeholder(cfg["platform"].get("manager_role")):
        _fail("platform.manager_role is required (the VAST role new UI logins join)")
    for bucket in _buckets(cfg):
        if _placeholder(bucket):
            _fail("a bucket name is still a placeholder")


def ensure(path: Path) -> None:
    cfg = _load(path)
    _require_declared(cfg)
    host, username, password, tenant = _login_material(path, cfg)
    vms = Vms(host, tenant, username, password)
    try:
        vms.login()
    except RuntimeError as exc:
        _fail(str(exc))
    _ok(f"VMS login as {username} on tenant {tenant}")
    try:
        tenant_row = _ensure_tenant(vms, tenant)
        tenant_id = int(tenant_row.get("id"))
        owner_name = str(cfg["platform"].get("s3_user") or "vastclaw").strip()
        if _placeholder(owner_name):
            _fail("platform.s3_user is unset")
        keys_ready = _keys_ready(path)
        user = None
        for row in vms.rows("/api/users/?page_size=500"):
            if row.get("name") == owner_name:
                user = row
                break
        if user is None and keys_ready:
            _fail(
                f"platform.s3_user {owner_name} does not exist, and a vast access key is already set. "
                "Point s3_user at the user that owns that key, or remove it so deploy can create one."
            )
        if user is None:
            user = _ensure_s3_user(vms, owner_name)
        else:
            _ok(f"S3 user {owner_name}")
        group_name = str(cfg["platform"].get("s3_group") or "").strip()
        if _placeholder(group_name):
            _fail("platform.s3_group is required")
        _ensure_user_group(vms, user, group_name)
        _ensure_keys(vms, int(user["id"]), path)
        cfg = _load(path)
        _ensure_managers(vms, cfg, tenant_id)
        _ensure_views(vms, cfg, tenant_id, owner_name)
    except RuntimeError as exc:
        _fail(str(exc))
    load = cfg["platform"].get("corpus_load")
    print(f"PLATFORM_CORPUS_LOAD={'true' if load else 'false'}")


def write_corpus_config(path: Path, dest: Path) -> None:
    """Ingest settings for the in-cluster corpus pod. Keys stay in this file only."""
    cfg = _load(path)
    vdb = cfg["vdb"]
    model = cfg["model"]
    platform = cfg["platform"]
    access_key, secret_key = resolved_keys(path)
    if _placeholder(access_key) or _placeholder(secret_key):
        _fail("vast access keys are still placeholders; run ensure first")
    bucket = str(platform.get("corpus_bucket") or "earnings-calls")
    schema = str(platform.get("corpus_schema") or "earnings21")
    table = str(platform.get("corpus_table") or "call_chunks")
    embed = str(model.get("embed_base_url") or "")
    dest.write_text(
        "\n".join(
            [
                "nim:",
                f"  base_url: {json.dumps(embed)}",
                f"  api_key: {json.dumps(str(model.get('embed_api_key') or ''))}",
                f"  embed_model: {json.dumps(str(model.get('embed_model') or 'nvidia/nv-embedqa-e5-v5'))}",
                f"  embed_dim: {int(model.get('embed_dim') or 1024)}",
                "vast:",
                f"  access_key: {json.dumps(access_key)}",
                f"  secret_key: {json.dumps(secret_key)}",
                "vdb:",
                f"  endpoint: {json.dumps(str(vdb.get('endpoint') or ''))}",
                f"  bucket: {json.dumps(bucket)}",
                f"  schema: {json.dumps(schema)}",
                f"  table: {json.dumps(table)}",
                "  ssl_verify: false",
                "s3:",
                f"  endpoint: {json.dumps(str(cfg['s3'].get('endpoint') or ''))}",
                "  ssl_verify: false",
                "ingest:",
                "  department: sales",
                "  data_dir: /corpus",
                "",
            ]
        )
    )
    print(f"corpus target {bucket}/{schema}.{table}")


def remove(path: Path, *, keep_corpus: bool = False) -> None:
    """Delete the buckets, S3 user, and UI logins this config created."""
    cfg = _load(path)
    host, username, password, tenant = _login_material(path, cfg)
    vms = Vms(host, tenant, username, password)
    try:
        vms.login()
    except RuntimeError as exc:
        _fail(str(exc))
    buckets = set(_buckets(cfg))
    corpus = str(cfg["platform"].get("corpus_bucket") or "earnings-calls").strip()
    if keep_corpus and corpus:
        buckets.discard(corpus)
    try:
        for bucket in sorted(buckets):
            _empty_database(vms, bucket)
        for view in vms.rows("/api/views/?page_size=500"):
            bucket = _view_bucket(view)
            if bucket not in buckets or not view.get("id"):
                continue
            if vms.delete(f"/api/views/{view['id']}/"):
                _ok(f"bucket {bucket} removed")
        still = sorted({
            _view_bucket(view)
            for view in vms.rows("/api/views/?page_size=500")
            if _view_bucket(view) in buckets
        })
        if still:
            _fail(
                "buckets still present after delete: "
                + ", ".join(still)
                + ". A DATABASE bucket cannot be deleted while a schema remains."
            )
        if keep_corpus and corpus:
            _ok(f"bucket {corpus} kept")
        owner = str(cfg["platform"].get("s3_user") or "").strip()
        if not keep_corpus and owner and not _placeholder(owner):
            for user in vms.rows("/api/users/?page_size=500"):
                if user.get("name") == owner and user.get("id"):
                    vms.delete(f"/api/users/{user['id']}/")
                    _ok(f"S3 user {owner} removed")
        elif keep_corpus and owner:
            _ok(f"S3 user {owner} kept")
        users = cfg["auth"].get("users") or {}
        if isinstance(users, dict):
            names = {
                str(name)
                for name, record in users.items()
                if isinstance(record, dict) and record.get("local") not in (True, "true", "True")
            }
            for manager in vms.rows("/api/managers/?page_size=500"):
                if manager.get("username") in names and manager.get("id"):
                    vms.delete(f"/api/managers/{manager['id']}/")
                    _ok(f"VMS login {manager.get('username')} removed")
    except RuntimeError as exc:
        _fail(str(exc))
    if not keep_corpus:
        side = access_file(path)
        if side.is_file():
            side.unlink()
            _ok(f"removed {side.name}")
    else:
        _ok("vast-access.yaml kept")
    if keep_corpus:
        _ok("VAST objects removed (corpus bucket, S3 user, tenant, view policy, and role kept)")
    else:
        _ok("VAST objects for this config removed (tenant, view policy, and role kept)")


def render(path: Path) -> None:
    """Declaration plus generated keys, with the VMS admin password removed."""
    text = path.read_text()
    access, secret = resolved_keys(path)
    if not _placeholder(access) and not _placeholder(secret):
        text = set_block_scalar(text, "vast", "access_key", access)
        text = set_block_scalar(text, "vast", "secret_key", secret)
    sys.stdout.write(redact_platform_password(text))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "corpus-config" and len(sys.argv) == 4:
        path = Path(sys.argv[2])
        if not path.is_file():
            _fail(f"config not found: {path}")
        write_corpus_config(path, Path(sys.argv[3]))
        return
    if len(sys.argv) != 3 or cmd not in ("ensure", "render", "remove", "remove-keep-corpus"):
        print(
            f"usage: {Path(sys.argv[0]).name} ensure|render|remove|remove-keep-corpus <workflow-ui.config.yaml>",
            file=sys.stderr,
        )
        print(
            f"       {Path(sys.argv[0]).name} corpus-config <workflow-ui.config.yaml> <ingest.yaml>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    path = Path(sys.argv[2])
    if not path.is_file():
        _fail(f"config not found: {path}")
    if cmd == "render":
        render(path)
        return
    if cmd == "remove":
        remove(path)
        return
    if cmd == "remove-keep-corpus":
        remove(path, keep_corpus=True)
        return
    ensure(path)


if __name__ == "__main__":
    main()
