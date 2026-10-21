
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from vastclaw_ui.config import McpSettings

_log = logging.getLogger("vastclaw.rag_client")


def _sync_coro(coro):  # type: ignore[no-untyped-def]
    """Run an async MCP client coroutine from sync code (FastAPI handlers, threads)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _tool_entries(listed: Any) -> list[Any]:
    """Normalize MCP list_tools responses across SDK versions."""
    if listed is None:
        return []
    tools = getattr(listed, "tools", None)
    if tools is None and isinstance(listed, dict):
        tools = listed.get("tools")
    if tools is None and isinstance(listed, list):
        tools = listed
    return list(tools or [])


def _tool_name_desc(entry: Any) -> tuple[str, str]:
    if isinstance(entry, dict):
        return str(entry.get("name") or ""), str(entry.get("description") or "").strip()
    return str(getattr(entry, "name", "") or ""), str(
        getattr(entry, "description", "") or ""
    ).strip()


class RagMcpClient:
    def __init__(self, settings: McpSettings, department: str) -> None:
        self._s = settings
        base = settings.base_url_template.format(department=department).rstrip("/")
        # FastMCP's SSE transport is served at /sse.
        self._url = f"{base}/sse"

    def transcript_search(
        self,
        embedding: list[float],
        limit: int = 6,
        engagement_id: str | None = None,
        speaker_type: str | None = None,
        customer: str | None = None,
        opportunity: str | None = None,
        sales_stage: str | None = None,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"embedding": embedding, "limit": limit}
        for key, val in (
            ("engagement_id", engagement_id),
            ("speaker_type", speaker_type),
            ("customer", customer),
            ("opportunity", opportunity),
            ("sales_stage", sales_stage),
            ("account_id", account_id),
        ):
            if val:
                args[key] = val
        return self._call("transcript_search", args)

    def list_call_metadata(self, limit: int = 50) -> dict[str, Any]:
        """Discover the corpus filter surface (calls / speaker_types / dates). Returns
        {} on any failure so callers can proceed unfiltered."""
        rows = self._call("list_call_metadata", {"limit": limit})
        return rows[0] if rows else {}

    async def _deadline(self, coro: Any) -> Any:
        """Bound every MCP round-trip by ``request_timeout_seconds``."""
        return await asyncio.wait_for(coro, timeout=self._s.request_timeout_seconds)

    def _call(self, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return _sync_coro(self._acall(name, args))
        except Exception as exc:  # transport / server / parse — degrade, never raise
            _log.warning("MCP tool %s failed at %s: %s", name, self._url, exc)
            return []

    async def _acall(self, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        async def run() -> Any:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(self._url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool(name, args)

        return _parse_tool_result(await self._deadline(run()))

    def list_tools(self) -> list[dict[str, str]]:
        try:
            return _sync_coro(self._alist_tools())
        except Exception as exc:
            _log.warning("MCP list_tools failed at %s: %s", self._url, exc)
            return []

    async def _alist_tools(self) -> list[dict[str, str]]:
        async def run() -> Any:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(self._url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.list_tools()

        listed = await self._deadline(run())
        out: list[dict[str, str]] = []
        for t in _tool_entries(listed):
            name, desc = _tool_name_desc(t)
            if name:
                out.append({"name": name, "description": desc})
        if not out:
            _log.warning("MCP list_tools returned no tools at %s (raw=%r)", self._url, listed)
        else:
            _log.info("MCP list_tools ok at %s: %s", self._url, [t["name"] for t in out])
        return out


def _parse_tool_result(result: Any) -> list[dict[str, Any]]:
    # Prefer structured content (newer MCP), fall back to JSON in the text content.
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict):
        # FastMCP wraps list returns as {"result": [...]}.
        data = data.get("result", data)
    if data is None:
        for item in getattr(result, "content", None) or []:
            text = getattr(item, "text", None)
            if not text:
                continue
            try:
                data = json.loads(text)
                break
            except ValueError:
                continue
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
