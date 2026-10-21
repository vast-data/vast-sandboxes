from __future__ import annotations

from typing import Any

import pyarrow as pa

WORKFLOWS_TABLE = "workflows"
WORKFLOW_TURNS_TABLE = "workflow_turns"
RUNS_TABLE = "runs"
ADMIN_AUDIT_TABLE = "admin_audit"
REPORTS_TABLE = "reports"
SPANS_TABLE = "spans"


def _strings(*names: str) -> list[pa.Field]:
    return [pa.field(name, pa.string()) for name in names]


def workflows_schema() -> pa.Schema:
    return pa.schema(
        _strings(
            "workflow_id",
            "department",
            "title",
            "created_by",
            "created_at",
            "updated_at",
            "status",
            "sandbox_view_path",
            "pvc_name",
            "last_run_id",
        )
    )


def workflow_turns_schema() -> pa.Schema:
    return pa.schema(
        [
            *_strings("turn_id", "workflow_id"),
            pa.field("seq", pa.int64()),
            *_strings("user", "department", "prompt_text", "created_at", "run_id", "status"),
        ]
    )


def runs_schema() -> pa.Schema:
    return pa.schema(
        _strings(
            "run_id",
            "workflow_id",
            "turn_id",
            "department_role",
            "started_at",
            "ended_at",
            "status",
        )
    )


def admin_audit_schema() -> pa.Schema:
    return pa.schema(
        _strings("action_id", "actor", "department", "action", "target", "ts", "result")
    )


def spans_schema() -> pa.Schema:
    return pa.schema(
        [
            *_strings(
                "trace_id",
                "span_id",
                "parent_span_id",
                "workflow_id",
                "run_id",
                "name",
                "kind",
                "agent_name",
                "department",
                "user",
                "start_time",
                "end_time",
                "status",
                "attributes_json",
            )
        ]
    )


def reports_schema() -> pa.Schema:
    return pa.schema(
        [
            *_strings("report_id", "workflow_id", "department"),
            pa.field("version", pa.int64()),
            *_strings("title", "created_by", "created_at"),
            pa.field("size", pa.int64()),
            pa.field("pdf", pa.binary()),
        ]
    )


CONTROL_TABLES: dict[str, Any] = {
    WORKFLOWS_TABLE: workflows_schema,
    WORKFLOW_TURNS_TABLE: workflow_turns_schema,
    RUNS_TABLE: runs_schema,
    ADMIN_AUDIT_TABLE: admin_audit_schema,
    REPORTS_TABLE: reports_schema,
}


_EXISTS_MARKERS = ("res=exist", "tabular_wwc", "alreadyexists")


def _is_already_exists(exc: Exception) -> bool:
    from vastdb.errors import Conflict

    if isinstance(exc, Conflict):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _EXISTS_MARKERS)


def _open_or_create_schema(bucket: Any, schema_name: str) -> Any:
    """Return a schema handle, creating it if absent. Treat 'already exists' as success."""
    schema = bucket.schema(schema_name, fail_if_missing=False)
    if schema is not None:
        return schema
    try:
        return bucket.create_schema(schema_name)
    except Exception as exc:
        if not _is_already_exists(exc):
            raise
        return bucket.schema(schema_name, fail_if_missing=True)


def _open_or_create_table(schema: Any, table_name: str, schema_fn: Any) -> None:
    if schema.table(table_name, fail_if_missing=False) is not None:
        return
    try:
        schema.create_table(table_name, schema_fn())
    except Exception as exc:
        if not _is_already_exists(exc):
            raise


def _ensure_tables(session: Any, bucket: str, schema_name: str, tables: dict[str, Any]) -> None:
    """Create every missing table, one transaction per table."""
    with session.transaction() as tx:
        _open_or_create_schema(tx.bucket(bucket), schema_name)

    failed: dict[str, Exception] = {}
    for name, schema_fn in tables.items():
        try:
            with session.transaction() as tx:
                schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=True)
                _open_or_create_table(schema, name, schema_fn)
        except Exception as exc:
            failed[name] = exc

    if failed:
        detail = "; ".join(f"{name}: {exc}" for name, exc in failed.items())
        raise RuntimeError(f"could not ensure table(s) in {bucket}/{schema_name} — {detail}")


def bootstrap_control_tables(session: Any, bucket: str, schema_name: str) -> None:
    """Idempotently create the event-sourced control schema and its tables."""
    _ensure_tables(session, bucket, schema_name, CONTROL_TABLES)


def bootstrap_spans_table(session: Any, bucket: str, schema_name: str) -> None:
    """Idempotently create the telemetry schema and the ``spans`` table."""
    _ensure_tables(session, bucket, schema_name, {SPANS_TABLE: spans_schema})
