from __future__ import annotations

import os

from pydantic import BaseModel, field_validator


class AgentIdentity(BaseModel):
    agent_name: str
    department: str
    user: str
    is_subagent: bool = False
    parent_agent: str | None = None

    @field_validator("agent_name", "department", "user")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must be non-empty")
        return value.strip()

    @property
    def bucket(self) -> str:
        # The department is the isolation boundary: one bucket per department.
        return f"vastclaw-{self.department}"

    @property
    def namespace(self) -> str:
        return f"vastclaw-{self.department}"

    @property
    def memory_owner(self) -> str:
        return self.agent_name

    @classmethod
    def from_env(cls) -> AgentIdentity:
        return cls(
            agent_name=os.environ["VASTCLAW_AGENT_NAME"],
            department=os.environ["VASTCLAW_DEPARTMENT"],
            user=os.environ.get("VASTCLAW_USER", "unknown"),
            is_subagent=os.environ.get("VASTCLAW_IS_SUBAGENT", "false").lower() == "true",
            parent_agent=os.environ.get("VASTCLAW_PARENT_AGENT") or None,
        )


class AccessDeniedError(RuntimeError):
    pass


def require_bucket_scope(identity: AgentIdentity, bucket: str) -> None:
    # Optional dept object-store naming when MCP s3_* tools are used:
    base = f"vastclaw-{identity.department}"
    if bucket != base and not bucket.startswith(f"{base}-"):
        raise AccessDeniedError(
            f"department '{identity.department}' may only access '{base}' or "
            f"'{base}-*', got '{bucket}'"
        )
