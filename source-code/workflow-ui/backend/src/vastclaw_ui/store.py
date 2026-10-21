from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from vastclaw_ui.config import VastCredentials, VastDBSettings


class Span(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    workflow_id: str
    run_id: str
    name: str
    kind: str = "internal"
    agent_name: str
    department: str
    user: str = "unknown"
    start_time: str
    end_time: str = ""
    status: str = "ok"
    attributes: dict[str, str] = Field(default_factory=dict)


class SpanSource(Protocol):
    def ensure_schema(self) -> None: ...

    def fetch(self, department: str | None) -> list[Span]: ...

    def write_spans(self, spans: list[Span]) -> None: ...

    def finalize_span(self, span_id: str, end_time: str, status: str) -> None: ...

    def update_span(
        self,
        span_id: str,
        *,
        end_time: str | None = None,
        status: str | None = None,
        attributes: dict[str, str] | None = None,
    ) -> None: ...

    def delete_workflow(self, workflow_id: str) -> int: ...

    def reset(self) -> None: ...


class VastDBSpanSource:
    def __init__(self, credentials: VastCredentials, settings: VastDBSettings) -> None:
        self._credentials = credentials
        self._settings = settings
        self._session: Any = None

    def _connect(self) -> Any:
        if self._session is None:
            import vastdb

            self._session = vastdb.connect(
                endpoint=self._settings.endpoint,
                access=self._credentials.access_key,
                secret=self._credentials.secret_key,
                ssl_verify=self._settings.ssl_verify,
            )
        return self._session

    def ensure_schema(self) -> None:
        """Idempotently create the telemetry schema + ``spans`` table."""
        from vastclaw_ui.schema import bootstrap_spans_table

        bootstrap_spans_table(
            self._connect(), self._settings.telemetry_bucket, self._settings.telemetry_schema
        )

    def fetch(self, department: str | None) -> list[Span]:
        session = self._connect()
        with session.transaction() as tx:
            schema = tx.bucket(self._settings.telemetry_bucket).schema(
                self._settings.telemetry_schema, fail_if_missing=False
            )
            table = None if schema is None else schema.table("spans", fail_if_missing=False)
            if table is None:
                return []
            predicate = None if department is None else (table["department"] == department)
            rows = table.select(predicate=predicate).read_all().to_pylist()
        spans: list[Span] = []
        for row in rows:
            attributes_raw = row.get("attributes_json") or "{}"
            spans.append(
                Span(
                    trace_id=row["trace_id"],
                    span_id=row["span_id"],
                    parent_span_id=row.get("parent_span_id", ""),
                    workflow_id=row["workflow_id"],
                    run_id=row["run_id"],
                    name=row["name"],
                    kind=row.get("kind", "internal"),
                    agent_name=row["agent_name"],
                    department=row["department"],
                    user=row.get("user", "unknown"),
                    start_time=row.get("start_time", ""),
                    end_time=row.get("end_time", ""),
                    status=row.get("status", "ok"),
                    attributes=json.loads(attributes_raw),
                )
            )
        return spans

    def _spans_table(self, tx: Any) -> Any:
        return (
            tx.bucket(self._settings.telemetry_bucket)
            .schema(self._settings.telemetry_schema)
            .table("spans")
        )

    def write_spans(self, spans: list[Span]) -> None:
        if not spans:
            return
        import pyarrow as pa

        table = pa.Table.from_pydict(
            {
                "trace_id": [s.trace_id for s in spans],
                "span_id": [s.span_id for s in spans],
                "parent_span_id": [s.parent_span_id for s in spans],
                "workflow_id": [s.workflow_id for s in spans],
                "run_id": [s.run_id for s in spans],
                "name": [s.name for s in spans],
                "kind": [s.kind for s in spans],
                "agent_name": [s.agent_name for s in spans],
                "department": [s.department for s in spans],
                "user": [s.user for s in spans],
                "start_time": [s.start_time for s in spans],
                "end_time": [s.end_time for s in spans],
                "status": [s.status for s in spans],
                "attributes_json": [json.dumps(s.attributes) for s in spans],
            }
        )
        session = self._connect()
        with session.transaction() as tx:
            self._spans_table(tx).insert(table)

    def finalize_span(self, span_id: str, end_time: str, status: str) -> None:
        """Set end_time/status on an already-written (running) span, in place."""
        import pyarrow as pa

        session = self._connect()
        with session.transaction() as tx:
            table = self._spans_table(tx)
            batch = table.select(
                predicate=(table["span_id"] == span_id), internal_row_id=True
            ).read_all()
            if batch.num_rows == 0:
                return
            row_ids = batch.column("$row_id")
            table.update(
                pa.table(
                    {
                        "$row_id": row_ids,
                        "end_time": pa.array([end_time] * batch.num_rows),
                        "status": pa.array([status] * batch.num_rows),
                    }
                ),
                columns=["end_time", "status"],
            )

    def update_span(
        self,
        span_id: str,
        *,
        end_time: str | None = None,
        status: str | None = None,
        attributes: dict[str, str] | None = None,
    ) -> None:
        """Update an already-written span in place (end_time / status / attributes)."""
        import pyarrow as pa

        cols: dict[str, str] = {}
        if end_time is not None:
            cols["end_time"] = end_time
        if status is not None:
            cols["status"] = status
        if attributes is not None:
            cols["attributes_json"] = json.dumps(attributes)
        if not cols:
            return
        session = self._connect()
        with session.transaction() as tx:
            table = self._spans_table(tx)
            batch = table.select(
                predicate=(table["span_id"] == span_id), internal_row_id=True
            ).read_all()
            if batch.num_rows == 0:
                return
            update_cols: dict[str, Any] = {"$row_id": batch.column("$row_id")}
            for key, val in cols.items():
                update_cols[key] = pa.array([val] * batch.num_rows)
            table.update(pa.table(update_cols), columns=list(cols.keys()))

    def delete_workflow(self, workflow_id: str) -> int:
        """Delete every span for one workflow. Returns rows removed. Idempotent:
        a missing schema/table is a no-op."""
        import pyarrow as pa

        session = self._connect()
        with session.transaction() as tx:
            schema = tx.bucket(self._settings.telemetry_bucket).schema(
                self._settings.telemetry_schema, fail_if_missing=False
            )
            table = None if schema is None else schema.table("spans", fail_if_missing=False)
            if table is None:
                return 0
            batch = table.select(
                predicate=(table["workflow_id"] == workflow_id), internal_row_id=True
            ).read_all()
            if not batch.num_rows:
                return 0
            table.delete(pa.table({"$row_id": batch.column("$row_id")}))
            return batch.num_rows

    def reset(self) -> None:
        """Delete every span (factory reset). Idempotent: a missing table is a no-op."""
        import pyarrow as pa

        session = self._connect()
        with session.transaction() as tx:
            schema = tx.bucket(self._settings.telemetry_bucket).schema(
                self._settings.telemetry_schema, fail_if_missing=False
            )
            table = None if schema is None else schema.table("spans", fail_if_missing=False)
            if table is None:
                return
            batch = table.select(internal_row_id=True).read_all()
            if batch.num_rows:
                table.delete(pa.table({"$row_id": batch.column("$row_id")}))
