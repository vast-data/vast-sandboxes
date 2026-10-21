from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from vastclaw_mcp.config import Settings
from vastclaw_mcp.identity import AgentIdentity
from vastclaw_mcp.models import MemoryRecord, StructuredQueryResult

MEMORY_TABLE = "agent_memory"


def _open_or_create_schema(bucket: Any, schema_name: str) -> Any:
    """Return a schema handle, creating it if absent."""
    schema = bucket.schema(schema_name, fail_if_missing=False)
    if schema is not None:
        return schema
    try:
        return bucket.create_schema(schema_name)
    except Exception as exc:
        from vastdb.errors import Conflict

        if isinstance(exc, Conflict):
            schema = bucket.schema(schema_name, fail_if_missing=False)
            if schema is not None:
                return schema
        raise


def _open_or_create_table(schema: Any, table_name: str, arrow_schema: pa.Schema) -> None:
    if schema.table(table_name, fail_if_missing=False) is not None:
        return
    try:
        schema.create_table(table_name, arrow_schema)
    except Exception as exc:
        from vastdb.errors import Conflict

        if not isinstance(exc, Conflict):
            raise


def _embedding_type(dim: int) -> pa.DataType:
    return pa.list_(pa.field("item", pa.float32(), nullable=False), dim)


class VastDBClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: Any = None

    def _connect(self) -> Any:
        if self._session is None:
            import vastdb

            self._session = vastdb.connect(
                endpoint=self._settings.vdb.endpoint,
                access=self._settings.vast.access_key,
                secret=self._settings.vast.secret_key,
                ssl_verify=self._settings.vdb.ssl_verify,
            )
        return self._session

    def _memory_schema(self) -> pa.Schema:
        dim = self._settings.vdb.embedding_dim
        return pa.schema(
            [
                pa.field("memory_id", pa.string()),
                pa.field("agent_name", pa.string()),
                pa.field("department", pa.string()),
                pa.field("scope", pa.string()),
                pa.field("content", pa.string()),
                pa.field("created_at", pa.string()),
                pa.field("embedding", _embedding_type(dim)),
            ]
        )

    def bootstrap(self) -> None:
        session = self._connect()
        bucket_name = self._settings.vdb.memory_bucket
        schema_name = self._settings.vdb.memory_schema
        with session.transaction() as tx:
            schema = _open_or_create_schema(tx.bucket(bucket_name), schema_name)
            _open_or_create_table(schema, MEMORY_TABLE, self._memory_schema())

    def write_memory(
        self, identity: AgentIdentity, content: str, embedding: list[float], scope: str
    ) -> MemoryRecord:
        session = self._connect()
        record = MemoryRecord(
            memory_id=str(uuid.uuid4()),
            agent_name=identity.agent_name,
            department=identity.department,
            scope=scope,
            content=content,
            created_at=datetime.now(UTC).isoformat(),
        )
        batch = pa.record_batch(
            {
                "memory_id": [record.memory_id],
                "agent_name": [record.agent_name],
                "department": [record.department],
                "scope": [record.scope],
                "content": [record.content],
                "created_at": [record.created_at],
                "embedding": pa.array(
                    [embedding],
                    type=_embedding_type(self._settings.vdb.embedding_dim),
                ),
            }
        )
        with session.transaction() as tx:
            table = (
                tx.bucket(self._settings.vdb.memory_bucket)
                .schema(self._settings.vdb.memory_schema)
                .table(MEMORY_TABLE)
            )
            table.insert(pa.Table.from_batches([batch]))
        return record

    def structured_query(
        self, identity: AgentIdentity, bucket: str, schema: str, table: str, limit: int
    ) -> StructuredQueryResult:
        session = self._connect()
        with session.transaction() as tx:
            handle = tx.bucket(bucket).schema(schema).table(table)
            reader = handle.select(
                predicate=(handle["department"] == identity.department),
                limit_rows=limit,
            )
            result = reader.read_all()
        return StructuredQueryResult(
            columns=result.schema.names,
            rows=[list(row.values()) for row in result.to_pylist()],
        )
