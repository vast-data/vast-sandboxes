"""Earnings-call RAG retrieval over the `call_chunks` corpus."""

from __future__ import annotations

from typing import Any

from vastclaw_mcp.config import Settings
from vastclaw_mcp.identity import AgentIdentity
from vastclaw_mcp.models import CallMetadata, MetadataFacet, SimilarCall, TranscriptHit


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class RagSearch:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _corpus_department(self, identity: AgentIdentity) -> str:
        """The `department` value to filter the corpus on."""
        return self._settings.vdb.rag_department_scope or identity.department

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

    def _table_ref(self) -> str:
        v = self._settings.vdb
        return f'"{v.rag_bucket}/{v.rag_schema}"."{v.rag_table}"'

    def _run(self, sql: str) -> list[dict]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql)
                names = [c[0] for c in cur.description]
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            conn.close()
        return [dict(zip(names, row, strict=True)) for row in rows]

    def transcript_search(
        self,
        identity: AgentIdentity,
        embedding: list[float],
        limit: int = 8,
        engagement_id: str | None = None,
        speaker_type: str | None = None,
        customer: str | None = None,
        opportunity: str | None = None,
        sales_stage: str | None = None,
        account_id: str | None = None,
    ) -> list[TranscriptHit]:
        """Nearest snippets to the query vector, optionally scoped by business metadata."""
        dim = self._settings.vdb.embedding_dim
        if len(embedding) != dim:
            raise ValueError(f"embedding must have length {dim}, got {len(embedding)}")
        vec = ",".join(repr(float(x)) for x in embedding)

        where = [f"department = {_quote(self._corpus_department(identity))}"]
        if engagement_id:
            where.append(f"engagement_id = {_quote(engagement_id)}")
        if speaker_type:
            where.append(f"speaker_type = {_quote(speaker_type)}")
        if account_id:
            where.append(f"account_id = {_quote(account_id)}")
        # Name filters are case-insensitive (the LLM rarely matches casing exactly).
        if customer:
            where.append(f"LOWER(customer) = {_quote(customer.lower())}")
        if opportunity:
            where.append(f"LOWER(opportunity) = {_quote(opportunity.lower())}")
        if sales_stage:
            where.append(f"LOWER(sales_stage) = {_quote(sales_stage.lower())}")

        table = self._settings.vdb.rag_table
        sql = (
            "SELECT chunk_id, engagement_id, customer, account_id, opportunity, opportunity_id, "
            "sales_stage, speaker_name, speaker_type, call_date, time_start, text, "
            f"array_cosine_distance(embedding::FLOAT[{dim}], ARRAY[{vec}]::FLOAT[{dim}]) AS distance, "
            f"{_quote(table)} AS vdb_table "
            f"FROM {self._table_ref()} WHERE {' AND '.join(where)} "
            f"ORDER BY distance ASC LIMIT {int(limit)}"
        )
        return [
            TranscriptHit(
                chunk_id=str(r["chunk_id"]),
                engagement_id=str(r["engagement_id"]),
                customer=str(r.get("customer") or ""),
                account_id=str(r.get("account_id") or ""),
                opportunity=str(r.get("opportunity") or ""),
                opportunity_id=str(r.get("opportunity_id") or ""),
                sales_stage=str(r.get("sales_stage") or ""),
                speaker_name=str(r["speaker_name"]),
                speaker_type=str(r["speaker_type"]),
                call_date=str(r.get("call_date") or ""),
                time_start=float(r["time_start"]),
                text=str(r["text"]),
                distance=float(r["distance"]),
                table=str(r.get("vdb_table") or table),
            )
            for r in self._run(sql)
        ]

    def list_call_metadata(
        self, identity: AgentIdentity, limit: int = 50
    ) -> CallMetadata:
        dept = _quote(self._corpus_department(identity))
        tbl = self._table_ref()

        def facets(column: str, order: str, cap: int) -> list[MetadataFacet]:
            sql = (
                f"SELECT {column} AS value, COUNT(*) AS n FROM {tbl} "
                f"WHERE department = {dept} AND {column} IS NOT NULL AND {column} <> '' "
                f"GROUP BY {column} ORDER BY {order} LIMIT {int(cap)}"
            )
            return [
                MetadataFacet(value=str(r["value"] if r["value"] is not None else ""), count=int(r["n"]))
                for r in self._run(sql)
            ]

        total_rows = self._run(f"SELECT COUNT(*) AS n FROM {tbl} WHERE department = {dept}")
        total = int(total_rows[0]["n"]) if total_rows else 0
        return CallMetadata(
            total_chunks=total,
            calls=facets("engagement_id", "n DESC", limit),
            customers=facets("customer", "n DESC", limit),
            opportunities=facets("opportunity", "n DESC", limit),
            sales_stages=facets("sales_stage", "n DESC", 25),
            speaker_types=facets("speaker_type", "n DESC", 10),
            call_dates=facets("call_date", "value ASC", limit),
        )

    def similar_calls(
        self, identity: AgentIdentity, embedding: list[float], limit: int = 5
    ) -> list[SimilarCall]:
        dim = self._settings.vdb.embedding_dim
        if len(embedding) != dim:
            raise ValueError(f"embedding must have length {dim}, got {len(embedding)}")
        vec = ",".join(repr(float(x)) for x in embedding)

        sql = (
            "SELECT engagement_id, customer, opportunity, sales_stage, "
            f"MIN(array_cosine_distance(embedding::FLOAT[{dim}], ARRAY[{vec}]::FLOAT[{dim}])) AS best_distance, "
            "COUNT(*) AS matched_snippets "
            f"FROM {self._table_ref()} WHERE department = {_quote(self._corpus_department(identity))} "
            "GROUP BY engagement_id, customer, opportunity, sales_stage ORDER BY best_distance ASC "
            f"LIMIT {int(limit)}"
        )
        return [
            SimilarCall(
                engagement_id=str(r["engagement_id"]),
                customer=str(r.get("customer") or ""),
                opportunity=str(r.get("opportunity") or ""),
                sales_stage=str(r.get("sales_stage") or ""),
                best_distance=float(r["best_distance"]),
                matched_snippets=int(r["matched_snippets"]),
            )
            for r in self._run(sql)
        ]
