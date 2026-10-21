"""VastDB table schema for the earnings-call RAG corpus (Earnings-21)."""

from __future__ import annotations

import pyarrow as pa


def build_schema(embedding_dim: int) -> pa.Schema:
    return pa.schema(
        [
            ("chunk_id", pa.string()),          # stable pk: <engagement>_<index>
            ("engagement_id", pa.string()),     # the call (Earnings-21 file_id)
            # --- Business context — company / quarter / sector for retrieval filters.
            ("account_id", pa.string()),
            ("customer", pa.string()),          # company name
            ("opportunity_id", pa.string()),
            ("opportunity", pa.string()),       # e.g. "General Electric Q1 2020 earnings"
            ("sales_stage", pa.string()),       # sector (e.g. Conglomerate, Financial)
            ("snippet_start_index", pa.int32()),
            ("snippet_end_index", pa.int32()),  # == start for snippet-granular ingest
            ("time_start", pa.float64()),        # seconds into call
            ("time_end", pa.float64()),
            ("call_date", pa.string()),
            ("speaker_name", pa.string()),
            ("speaker_type", pa.string()),       # company | analyst | operator
            ("participant", pa.int64()),
            ("engaging", pa.bool_()),
            ("word_count", pa.int32()),
            ("text", pa.string()),
            ("department", pa.string()),         # ACL / tenancy boundary
            ("ingest_version", pa.int64()),      # epoch seconds; enables incremental re-ingest
            (
                "embedding",
                pa.list_(pa.field("item", pa.float32(), nullable=False), embedding_dim),
            ),
        ]
    )


