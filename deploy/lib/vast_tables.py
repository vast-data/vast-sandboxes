#!/usr/bin/env python3
"""Create or drop the VastDB schemas and tables for this deploy."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pyarrow as pa

from yaml_subset import extract_top_map


def _fail(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"✓ {msg}")


def _strings(*names: str) -> list[pa.Field]:
    return [pa.field(name, pa.string()) for name in names]


def _embedding(dim: int) -> pa.DataType:
    return pa.list_(pa.field("item", pa.float32(), nullable=False), dim)


def _exists(exc: Exception) -> bool:
    try:
        from vastdb.errors import Conflict

        if isinstance(exc, Conflict):
            return True
    except Exception:
        pass
    message = str(exc).lower()
    return any(marker in message for marker in ("res=exist", "tabular_wwc", "alreadyexists"))


def _ensure_schema(session: Any, bucket: str, schema_name: str) -> None:
    with session.transaction() as tx:
        handle = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
        if handle is not None:
            return
        try:
            tx.bucket(bucket).create_schema(schema_name)
        except Exception as exc:
            if not _exists(exc):
                raise
    _ok(f"schema {bucket}/{schema_name}")


def _ensure_table(session: Any, bucket: str, schema_name: str, table: str, arrow: pa.Schema) -> str:
    """Return 'exists' or 'created'."""
    with session.transaction() as tx:
        schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
        if schema is None:
            schema = tx.bucket(bucket).create_schema(schema_name)
        if schema.table(table, fail_if_missing=False) is not None:
            _ok(f"table {bucket}/{schema_name}.{table} already exists")
            return "exists"
        try:
            schema.create_table(table, arrow)
        except Exception as exc:
            if not _exists(exc):
                raise
            _ok(f"table {bucket}/{schema_name}.{table} already exists")
            return "exists"
    _ok(f"table {bucket}/{schema_name}.{table} created")
    return "created"


def _row_count(session: Any, bucket: str, schema_name: str, table: str) -> int:
    with session.transaction() as tx:
        schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
        if schema is None:
            return 0
        handle = schema.table(table, fail_if_missing=False)
        if handle is None:
            return 0
        return int(handle.get_stats().num_rows)


def _missing_bucket(exc: BaseException) -> bool:
    return type(exc).__name__ == "MissingBucket" or "MissingBucket" in str(exc)


def _drop_schema(session: Any, bucket: str, schema_name: str) -> None:
    with session.transaction() as tx:
        schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
        if schema is None:
            _ok(f"schema {bucket}/{schema_name} already absent")
            return
        names = list(schema.tablenames())
    for name in names:
        with session.transaction() as tx:
            schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
            if schema is None:
                break
            table = schema.table(name, fail_if_missing=False)
            if table is None:
                continue
            table.drop()
        _ok(f"dropped {bucket}/{schema_name}.{name}")
    with session.transaction() as tx:
        schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
        if schema is None:
            return
        schema.drop()
    _ok(f"dropped schema {bucket}/{schema_name}")


def _control(name: str) -> pa.Schema:
    if name == "workflows":
        return pa.schema(
            _strings(
                "workflow_id", "department", "title", "created_by", "created_at",
                "updated_at", "status", "sandbox_view_path", "pvc_name", "last_run_id",
            )
        )
    if name == "workflow_turns":
        return pa.schema(
            [
                *_strings("turn_id", "workflow_id"),
                pa.field("seq", pa.int64()),
                *_strings("user", "department", "prompt_text", "created_at", "run_id", "status"),
            ]
        )
    if name == "runs":
        return pa.schema(
            _strings(
                "run_id", "workflow_id", "turn_id", "department_role",
                "started_at", "ended_at", "status",
            )
        )
    if name == "admin_audit":
        return pa.schema(_strings("action_id", "actor", "department", "action", "target", "ts", "result"))
    if name == "reports":
        return pa.schema(
            [
                *_strings("report_id", "workflow_id", "department"),
                pa.field("version", pa.int64()),
                *_strings("title", "created_by", "created_at"),
                pa.field("size", pa.int64()),
                pa.field("pdf", pa.binary()),
            ]
        )
    raise RuntimeError(f"unknown control table {name}")


def _spans() -> pa.Schema:
    return pa.schema(
        _strings(
            "trace_id", "span_id", "parent_span_id", "workflow_id", "run_id",
            "name", "kind", "agent_name", "department", "user",
            "start_time", "end_time", "status", "attributes_json",
        )
    )


def _memory(dim: int) -> pa.Schema:
    return pa.schema(
        [
            *_strings("memory_id", "agent_name", "department", "scope", "content", "created_at"),
            pa.field("embedding", _embedding(dim)),
        ]
    )


def _corpus(dim: int) -> pa.Schema:
    return pa.schema(
        [
            *_strings("chunk_id", "engagement_id", "account_id", "customer", "opportunity_id", "opportunity", "sales_stage"),
            pa.field("snippet_start_index", pa.int32()),
            pa.field("snippet_end_index", pa.int32()),
            pa.field("time_start", pa.float64()),
            pa.field("time_end", pa.float64()),
            *_strings("call_date", "speaker_name", "speaker_type"),
            pa.field("participant", pa.int64()),
            pa.field("engaging", pa.bool_()),
            pa.field("word_count", pa.int32()),
            *_strings("text", "department"),
            pa.field("ingest_version", pa.int64()),
            pa.field("embedding", _embedding(dim)),
        ]
    )


def _targets(text: str) -> dict[str, Any]:
    vast = extract_top_map(text, "vast")
    vdb = extract_top_map(text, "vdb")
    model = extract_top_map(text, "model")
    platform = extract_top_map(text, "platform")
    departments = extract_top_map(text, "departments")
    database = str(platform.get("database_bucket") or vdb.get("telemetry_bucket") or "vastclaw-db")
    dept_names = list(departments) or ["sales", "finance"]
    return {
        "vast": vast,
        "vdb": vdb,
        "dim": int(model.get("embed_dim") or 1024),
        "database": database,
        "control_bucket": str(vdb.get("control_bucket") or database),
        "control_schema": str(vdb.get("control_schema") or "vastclaw_control"),
        "telemetry_schema": str(vdb.get("telemetry_schema") or "telemetry"),
        "corpus_bucket": str(platform.get("corpus_bucket") or "earnings-calls"),
        "corpus_schema": str(platform.get("corpus_schema") or "earnings21"),
        "corpus_table": str(platform.get("corpus_table") or "call_chunks"),
        "departments": dept_names,
    }


def _connect(spec: dict[str, Any]) -> Any:
    import vastdb

    vdb = spec["vdb"]
    vast = spec["vast"]
    return vastdb.connect(
        endpoint=str(vdb.get("endpoint") or ""),
        access=str(vast.get("access_key") or ""),
        secret=str(vast.get("secret_key") or ""),
        ssl_verify=bool(vdb.get("ssl_verify", False)),
    )


def ensure(spec: dict[str, Any]) -> None:
    session = _connect(spec)
    dim = spec["dim"]
    database = spec["database"]
    control_bucket = spec["control_bucket"]
    control_schema = spec["control_schema"]
    telemetry_schema = spec["telemetry_schema"]
    corpus_bucket = spec["corpus_bucket"]
    corpus_schema = spec["corpus_schema"]
    corpus_table = spec["corpus_table"]

    _ensure_schema(session, control_bucket, control_schema)
    for name in ("workflows", "workflow_turns", "runs", "admin_audit", "reports"):
        _ensure_table(session, control_bucket, control_schema, name, _control(name))
    _ensure_schema(session, database, telemetry_schema)
    _ensure_table(session, database, telemetry_schema, "spans", _spans())
    for dept in spec["departments"]:
        schema_name = f"memory_{dept}"
        _ensure_schema(session, database, schema_name)
        _ensure_table(session, database, schema_name, "agent_memory", _memory(dim))

    _ensure_schema(session, corpus_bucket, corpus_schema)
    corpus_state = _ensure_table(
        session, corpus_bucket, corpus_schema, corpus_table, _corpus(dim)
    )
    rows = _row_count(session, corpus_bucket, corpus_schema, corpus_table) if corpus_state == "exists" else 0
    if corpus_state == "exists" and rows > 0:
        _ok(f"corpus {corpus_bucket}/{corpus_schema}.{corpus_table} has {rows} rows")
        print("CORPUS_INGEST=skip")
    else:
        print("CORPUS_INGEST=run")
    _ok("VastDB schemas and tables ready")


def drop(spec: dict[str, Any], *, keep_corpus: bool = False) -> None:
    session = _connect(spec)
    database = spec["database"]
    corpus_key = (spec["corpus_bucket"], spec["corpus_schema"])
    schemas = [
        (spec["control_bucket"], spec["control_schema"]),
        (database, spec["telemetry_schema"]),
        corpus_key,
    ]
    seen = set(schemas)
    for dept in list(spec["departments"]) + ["memory"]:
        item = (database, f"memory_{dept}" if dept != "memory" else "memory")
        if item not in seen:
            schemas.append(item)
            seen.add(item)
    missing: set[str] = set()
    for bucket, schema_name in schemas:
        if keep_corpus and (bucket, schema_name) == corpus_key:
            _ok(f"kept corpus {bucket}/{schema_name}")
            continue
        if bucket in missing:
            continue
        try:
            _drop_schema(session, bucket, schema_name)
        except Exception as exc:
            if not _missing_bucket(exc):
                raise
            missing.add(bucket)
            _ok(f"bucket {bucket} already absent")
    if keep_corpus:
        _ok("VastDB schemas and tables removed (corpus kept)")
    else:
        _ok("VastDB schemas and tables removed")


def main() -> None:
    if len(sys.argv) not in (2, 3):
        _fail(f"usage: {Path(sys.argv[0]).name} <workflow-ui.config.yaml> [ensure|drop]")
    path = Path(sys.argv[1])
    action = sys.argv[2] if len(sys.argv) == 3 else "ensure"
    if action not in ("ensure", "drop", "drop-keep-corpus"):
        _fail(f"usage: {Path(sys.argv[0]).name} <workflow-ui.config.yaml> [ensure|drop|drop-keep-corpus]")
    if not path.is_file():
        _fail(f"config not found: {path}")
    text = path.read_text()
    spec = _targets(text)
    if action == "drop":
        drop(spec)
        return
    if action == "drop-keep-corpus":
        drop(spec, keep_corpus=True)
        return
    ensure(spec)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        _fail(str(exc))
