"""VastDB writer: connect, ensure schema/table, idempotent per-call upsert."""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from temp_ingest.config import Settings
from temp_ingest.schema import build_schema
from temp_ingest import vastdb_patch  # noqa: F401  (import installs the SDK vector-select patch)


class VastDBWriter:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        self._session: Any = None
        self._schema = build_schema(settings.nim.embed_dim)

    def _connect(self) -> Any:
        if self._session is None:
            import vastdb

            self._session = vastdb.connect(
                endpoint=self._s.vdb.endpoint,
                access=self._s.vast.access_key,
                secret=self._s.vast.secret_key,
                ssl_verify=self._s.vdb.ssl_verify,
            )
        return self._session

    def ensure_table(self) -> None:
        """Create the schema + table if missing. The bucket must already exist on the
        cluster (provisioned by admin/terraform)."""
        session = self._connect()
        with session.transaction() as tx:
            bucket = tx.bucket(self._s.vdb.bucket)
            schema = bucket.schema(self._s.vdb.schema_name, fail_if_missing=False)
            if schema is None:
                schema = bucket.create_schema(self._s.vdb.schema_name)
            table = schema.table(self._s.vdb.table, fail_if_missing=False)
            if table is None:
                schema.create_table(self._s.vdb.table, self._schema)

    def _table(self, tx: Any) -> Any:
        return (
            tx.bucket(self._s.vdb.bucket)
            .schema(self._s.vdb.schema_name)
            .table(self._s.vdb.table)
        )

    def delete_call(self, engagement_id: str) -> int:
        """Delete all rows for one call (idempotent re-ingest). Returns rows removed."""
        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx)
            batch = table.select(
                columns=["engagement_id"],
                predicate=(table["engagement_id"] == engagement_id),
                internal_row_id=True,
            ).read_all()
            if batch.num_rows == 0:
                return 0
            table.delete(pa.table({"$row_id": batch.column("$row_id")}))
            return batch.num_rows

    def write(self, arrow_table: pa.Table) -> int:
        session = self._connect()
        with session.transaction() as tx:
            self._table(tx).insert(arrow_table)
        return arrow_table.num_rows

    def upsert_call(self, engagement_id: str, arrow_table: pa.Table) -> int:
        self.delete_call(engagement_id)
        return self.write(arrow_table)
