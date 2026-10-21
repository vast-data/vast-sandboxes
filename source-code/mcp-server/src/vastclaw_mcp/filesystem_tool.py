from __future__ import annotations

from pathlib import Path

from vastclaw_mcp.config import Settings
from vastclaw_mcp.identity import AccessDeniedError, AgentIdentity
from vastclaw_mcp.models import FileEntry


class FilesystemTool:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _agent_root(self, identity: AgentIdentity) -> Path:
        root = (self._settings.sandbox.root / identity.agent_name).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _resolve(self, identity: AgentIdentity, relative: str) -> Path:
        root = self._agent_root(identity)
        target = (root / relative.lstrip("/")).resolve()
        if root != target and root not in target.parents:
            raise AccessDeniedError(
                f"path '{relative}' escapes the sandbox for agent '{identity.agent_name}'"
            )
        return target

    def list_dir(self, identity: AgentIdentity, relative: str = ".") -> list[FileEntry]:
        target = self._resolve(identity, relative)
        if not target.exists():
            return []
        return [
            FileEntry(
                path=str(child.relative_to(self._agent_root(identity))),
                is_dir=child.is_dir(),
                size=child.stat().st_size if child.is_file() else 0,
            )
            for child in sorted(target.iterdir())
        ]

    def read_file(self, identity: AgentIdentity, relative: str) -> str:
        target = self._resolve(identity, relative)
        data = target.read_bytes()[: self._settings.sandbox.max_read_bytes]
        return data.decode("utf-8", errors="replace")

    def write_file(self, identity: AgentIdentity, relative: str, content: str) -> FileEntry:
        target = self._resolve(identity, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return FileEntry(
            path=str(target.relative_to(self._agent_root(identity))),
            is_dir=False,
            size=target.stat().st_size,
        )
