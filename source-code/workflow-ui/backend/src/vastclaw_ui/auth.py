from __future__ import annotations

import time
from typing import Protocol

import jwt

from vastclaw_ui.config import AuthSettings
from vastclaw_ui.models import User


class InvalidCredentialsError(RuntimeError):
    pass


class Authenticator(Protocol):
    def authenticate(self, username: str, password: str) -> User: ...


class ConfigAuthenticator:
    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings

    def authenticate(self, username: str, password: str) -> User:
        record = self._settings.users.get(username)
        if record is None or record.password is None or record.password != password:
            raise InvalidCredentialsError("invalid username or password")
        return User(
            username=username,
            department=record.department,
            is_admin=record.is_admin,
        )


class VmsAuthenticator:
    """Hybrid authenticator matching the genomics-engine / HCLS reference apps."""

    VMS_TIMEOUT_SECONDS = 15.0

    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings
        host = settings.vms_host
        if host and not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        port = settings.vms_port
        if host and port and port != 443 and ":" not in host.split("//", 1)[-1]:
            host = f"{host}:{port}"
        self._host = host

    def authenticate(self, username: str, password: str) -> User:
        record = self._settings.users.get(username)
        if record is None:
            raise InvalidCredentialsError("invalid username or password")
        if record.local:
            if record.password is None or record.password != password:
                raise InvalidCredentialsError("invalid username or password")
        else:
            if not self._host:
                raise InvalidCredentialsError("VMS host not configured for VAST authentication")
            if not self._vms_ok(username, password):
                raise InvalidCredentialsError("invalid username or password")
        return User(
            username=username,
            department=record.department,
            is_admin=record.is_admin,
        )

    def _vms_ok(self, username: str, password: str) -> bool:
        # stdlib only (httpx is not a backend runtime dependency). Tenant-scoped
        import json
        import ssl
        import urllib.error
        import urllib.request

        ctx = ssl._create_unverified_context()
        payload = json.dumps({"username": username, "password": password}).encode()
        urls = []
        if self._settings.tenant_name:
            urls.append(f"{self._host}/api/token/{self._settings.tenant_name}/")
        urls.append(f"{self._host}/api/token/")
        for url in urls:
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.VMS_TIMEOUT_SECONDS, context=ctx
                ) as resp:
                    if resp.status == 200:
                        return True
            except urllib.error.HTTPError:
                # Wrong tenant path / bad creds on this URL -> try the next one.
                continue
            except OSError:
                # VMS unreachable/timeout -> treat as auth failure (no bypass).
                continue
        return False


class TokenService:
    def __init__(self, settings: AuthSettings) -> None:
        self._settings = settings

    def issue(self, user: User) -> str:
        now = int(time.time())
        payload = {
            "sub": user.username,
            "department": user.department,
            "is_admin": user.is_admin,
            "iat": now,
            "exp": now + self._settings.jwt_ttl_seconds,
        }
        return jwt.encode(payload, self._settings.jwt_secret, algorithm="HS256")

    def verify(self, token: str) -> User:
        try:
            payload = jwt.decode(token, self._settings.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise InvalidCredentialsError("invalid or expired token") from exc
        return User(
            username=str(payload["sub"]),
            department=str(payload["department"]),
            is_admin=bool(payload["is_admin"]),
        )
