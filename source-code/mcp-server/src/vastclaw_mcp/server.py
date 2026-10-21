from __future__ import annotations

from typing import Any

from vastclaw_mcp.config import Settings
from vastclaw_mcp.filesystem_tool import FilesystemTool
from vastclaw_mcp.identity import AgentIdentity
from vastclaw_mcp.models import (
    CallMetadata,
    FileEntry,
    MemoryRecord,
    S3ObjectRef,
    SimilarCall,
    StructuredQueryResult,
    TranscriptHit,
)
from vastclaw_mcp.rag_search import RagSearch
from vastclaw_mcp.s3_tool import S3Tool
from vastclaw_mcp.vastdb_client import VastDBClient
from vastclaw_mcp.vector_search import VectorSearch


class ToolRegistry:
    def __init__(self, settings: Settings, identity: AgentIdentity) -> None:
        self.settings = settings
        self.identity = identity
        self.vastdb = VastDBClient(settings)
        self.vectors = VectorSearch(settings)
        self.rag = RagSearch(settings)
        self.s3 = S3Tool(settings)
        self.fs = FilesystemTool(settings)

    def memory_recall(
        self, embedding: list[float], limit: int = 5, scope: str = "agent"
    ) -> list[MemoryRecord]:
        return self.vectors.recall(self.identity, embedding, limit, scope)

    def memory_write(
        self, content: str, embedding: list[float], scope: str = "agent"
    ) -> MemoryRecord:
        return self.vastdb.write_memory(self.identity, content, embedding, scope)

    def structured_query(
        self, bucket: str, schema: str, table: str, limit: int = 100
    ) -> StructuredQueryResult:
        return self.vastdb.structured_query(self.identity, bucket, schema, table, limit)

    def transcript_search(
        self,
        embedding: list[float],
        limit: int = 8,
        engagement_id: str | None = None,
        speaker_type: str | None = None,
        customer: str | None = None,
        opportunity: str | None = None,
        sales_stage: str | None = None,
        account_id: str | None = None,
    ) -> list[TranscriptHit]:
        return self.rag.transcript_search(
            self.identity,
            embedding,
            limit,
            engagement_id=engagement_id,
            speaker_type=speaker_type,
            customer=customer,
            opportunity=opportunity,
            sales_stage=sales_stage,
            account_id=account_id,
        )

    def similar_calls(self, embedding: list[float], limit: int = 5) -> list[SimilarCall]:
        return self.rag.similar_calls(self.identity, embedding, limit)

    def list_call_metadata(self, limit: int = 50) -> CallMetadata:
        return self.rag.list_call_metadata(self.identity, limit)

    def s3_list(self, prefix: str = "", limit: int = 100) -> list[S3ObjectRef]:
        return self.s3.list_objects(self.identity, prefix, limit)

    def s3_get_text(self, key: str) -> str:
        return self.s3.get_object(self.identity, key).decode("utf-8", errors="replace")

    def s3_put_text(self, key: str, content: str) -> S3ObjectRef:
        return self.s3.put_object(self.identity, key, content.encode("utf-8"))

    def fs_list(self, path: str = ".") -> list[FileEntry]:
        return self.fs.list_dir(self.identity, path)

    def fs_read(self, path: str) -> str:
        return self.fs.read_file(self.identity, path)

    def fs_write(self, path: str, content: str) -> FileEntry:
        return self.fs.write_file(self.identity, path, content)

def build_server(registry: ToolRegistry, host: str = "0.0.0.0", port: int = 8080) -> tuple[Any, bool]:
    """Return (server, legacy_fastmcp). legacy=True => mcp.server.fastmcp (host/port on ctor)."""
    try:
        from mcp.server.fastmcp import FastMCP

        server = FastMCP("vastclaw", host=host, port=port)
        legacy = True
    except ModuleNotFoundError:
        from fastmcp import FastMCP

        server = FastMCP("vastclaw")
        legacy = False

    server.tool(name="memory_recall")(registry.memory_recall)
    server.tool(name="memory_write")(registry.memory_write)
    server.tool(name="structured_query")(registry.structured_query)
    server.tool(name="transcript_search")(registry.transcript_search)
    server.tool(name="similar_calls")(registry.similar_calls)
    server.tool(name="list_call_metadata")(registry.list_call_metadata)
    server.tool(name="s3_list")(registry.s3_list)
    server.tool(name="s3_get_text")(registry.s3_get_text)
    server.tool(name="s3_put_text")(registry.s3_put_text)
    server.tool(name="fs_list")(registry.fs_list)
    server.tool(name="fs_read")(registry.fs_read)
    server.tool(name="fs_write")(registry.fs_write)

    return server, legacy


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="vastclaw-mcp")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport. 'stdio' is for local piping; the Kubernetes deployment uses "
        "'sse' to expose an HTTP server that stays up.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    settings = Settings.from_yaml()
    identity = AgentIdentity.from_env()
    registry = ToolRegistry(settings, identity)
    try:
        registry.vastdb.bootstrap()
        import logging

        logging.getLogger("vastclaw.mcp").info(
            "memory schema ready (%s/%s/agent_memory)",
            settings.vdb.memory_bucket,
            settings.vdb.memory_schema,
        )
    except Exception as exc:
        import logging

        logging.getLogger("vastclaw.mcp").warning(
            "memory schema bootstrap skipped (%s/%s): %s",
            settings.vdb.memory_bucket,
            settings.vdb.memory_schema,
            exc,
        )
    server, legacy = build_server(registry, host=args.host, port=args.port)

    if args.transport == "stdio":
        server.run()
    elif legacy:
        server.run(transport=args.transport)
    else:
        server.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
