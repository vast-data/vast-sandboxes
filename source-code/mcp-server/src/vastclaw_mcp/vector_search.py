from __future__ import annotations

from typing import Any

from vastclaw_mcp.config import Settings
from vastclaw_mcp.identity import AgentIdentity
from vastclaw_mcp.models import MemoryRecord
from vastclaw_mcp.vastdb_client import MEMORY_TABLE


class VectorSearch:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _connect(self) -> Any:
        import adbc_driver_manager.dbapi

        return adbc_driver_manager.dbapi.connect(
            driver=self._settings.vdb.adbc_driver_path,
            db_kwargs={
                "vast.db.endpoint": self._settings.vdb.endpoint,
                "vast.db.access_key": self._settings.vast.access_key,
                "vast.db.secret_key": self._settings.vast.secret_key,
            },
        )

    def recall(
        self, identity: AgentIdentity, embedding: list[float], limit: int, scope: str
    ) -> list[MemoryRecord]:
        dim = self._settings.vdb.embedding_dim
        if len(embedding) != dim:
            raise ValueError(f"embedding must have length {dim}, got {len(embedding)}")

        vector_sql = ",".join(repr(float(x)) for x in embedding)
        table_ref = f'"{self._settings.vdb.memory_bucket}/{self._settings.vdb.memory_schema}"."{MEMORY_TABLE}"'
        allowed_scopes = ["agent", "team"] if scope == "team" else ["agent"]
        scope_list = ",".join(f"'{s}'" for s in allowed_scopes)

        query = (
            "SELECT memory_id, agent_name, department, scope, content, created_at, "
            f"array_cosine_distance(embedding::FLOAT[{dim}], "
            f"ARRAY[{vector_sql}]::FLOAT[{dim}]) AS distance "
            f"FROM {table_ref} "
            f"WHERE department = '{identity.department}' "
            f"AND scope IN ({scope_list}) "
            f"AND (scope = 'team' OR agent_name = '{identity.agent_name}') "
            "ORDER BY distance ASC "
            f"LIMIT {int(limit)}"
        )

        records: list[MemoryRecord] = []
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(query)
                columns = [c[0] for c in cursor.description]
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()
        for row in rows:
            data = dict(zip(columns, row, strict=True))
            records.append(
                MemoryRecord(
                    memory_id=str(data["memory_id"]),
                    agent_name=str(data["agent_name"]),
                    department=str(data["department"]),
                    scope=str(data["scope"]),
                    content=str(data["content"]),
                    created_at=str(data["created_at"]),
                    distance=float(data["distance"]),
                )
            )
        return records
