"""Ingest core: embed per-call parquet and upsert into VastDB."""

from __future__ import annotations

import argparse
import glob
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from temp_ingest.config import Settings
from temp_ingest.embeddings import NimEmbedder
from temp_ingest.schema import build_schema
from temp_ingest.vastdb_writer import VastDBWriter


def _f(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return default


def _i(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (ValueError, AttributeError, TypeError):
        return default


def _build_arrow(
    rows: list[dict], embeddings: list[list[float]], settings: Settings
) -> pa.Table:
    dim = settings.nim.embed_dim
    dept = settings.ingest.department
    version = int(time.time())

    cols: dict[str, list] = {
        "chunk_id": [], "engagement_id": [],
        "account_id": [], "customer": [], "opportunity_id": [], "opportunity": [],
        "sales_stage": [], "snippet_start_index": [],
        "snippet_end_index": [], "time_start": [], "time_end": [], "call_date": [],
        "speaker_name": [], "speaker_type": [], "participant": [], "engaging": [],
        "word_count": [], "text": [], "department": [], "ingest_version": [],
    }
    for r in rows:
        idx = _i(r.get("index"))
        t0 = _f(r.get("snippet_time"))
        cols["chunk_id"].append(str(r.get("id") or f"{r.get('engagement_id')}_{idx}"))
        cols["engagement_id"].append(str(r.get("engagement_id") or ""))
        cols["account_id"].append(str(r.get("account_id") or ""))
        cols["customer"].append(str(r.get("customer") or ""))
        cols["opportunity_id"].append(str(r.get("opportunity_id") or ""))
        cols["opportunity"].append(str(r.get("opportunity") or ""))
        cols["sales_stage"].append(str(r.get("current_sales_stage_name") or r.get("sales_stage") or ""))
        cols["snippet_start_index"].append(idx)
        cols["snippet_end_index"].append(idx)
        cols["time_start"].append(t0)
        cols["time_end"].append(t0 + _f(r.get("snippet_length")))
        cols["call_date"].append(str(r.get("date_time") or ""))
        cols["speaker_name"].append(str(r.get("speaker_name") or ""))
        cols["speaker_type"].append(str(r.get("speaker_type") or ""))
        cols["participant"].append(_i(r.get("participant")))
        cols["engaging"].append(str(r.get("engaging")).strip().lower() == "true")
        cols["word_count"].append(_i(r.get("snippet_word_count")))
        cols["text"].append(str(r.get("snippet") or ""))
        cols["department"].append(dept)
        cols["ingest_version"].append(version)

    arrays = {name: pa.array(values) for name, values in cols.items()}
    arrays["snippet_start_index"] = pa.array(cols["snippet_start_index"], pa.int32())
    arrays["snippet_end_index"] = pa.array(cols["snippet_end_index"], pa.int32())
    arrays["participant"] = pa.array(cols["participant"], pa.int64())
    arrays["word_count"] = pa.array(cols["word_count"], pa.int32())
    arrays["ingest_version"] = pa.array(cols["ingest_version"], pa.int64())
    arrays["embedding"] = pa.array(
        embeddings, type=pa.list_(pa.field("item", pa.float32(), nullable=False), dim)
    )
    return pa.table(arrays, schema=build_schema(dim))


def ingest_call_file(
    path: str | Path,
    settings: Settings,
    embedder: NimEmbedder,
    writer: VastDBWriter,
) -> int:
    """Ingest one per-call parquet file. Returns the number of rows written."""
    rows = pq.read_table(path).to_pylist()
    if not rows:
        return 0
    engagement_id = str(rows[0].get("engagement_id") or Path(path).stem)
    texts = [str(r.get("snippet") or "") for r in rows]
    embeddings = embedder.embed(texts, input_type="passage")
    arrow = _build_arrow(rows, embeddings, settings)
    return writer.upsert_call(engagement_id, arrow)


def main() -> int:
    settings = Settings.load()
    parser = argparse.ArgumentParser(prog="temp_ingest.ingest")
    parser.add_argument("--one", help="ingest a single per-call parquet file")
    parser.add_argument("--data-dir", default=settings.ingest.data_dir)
    parser.add_argument(
        "--max-calls",
        type=int,
        default=0,
        help="cap how many calls (parquet files) to ingest; 0 = all",
    )
    parser.add_argument(
        "--start-after",
        help="resume: skip parquet files until after this engagement_id (sorted filename stem)",
    )
    args = parser.parse_args()

    embedder = NimEmbedder(settings.nim)
    writer = VastDBWriter(settings)
    writer.ensure_table()

    files = [args.one] if args.one else sorted(glob.glob(os.path.join(args.data_dir, "*.parquet")))
    if not files:
        print(f"no parquet files found (looked in {args.data_dir}); run prepare first")
        return 1
    if args.start_after:
        stem = Path(args.start_after).stem
        before = len(files)
        files = [f for f in files if Path(f).stem > stem]
        print(f"resume: skipping {before - len(files)} call(s), starting after {stem}")
    if args.max_calls and args.max_calls > 0:
        files = files[: args.max_calls]
        print(f"limiting to {len(files)} call(s) (--max-calls)")

    total = 0
    for i, f in enumerate(files, 1):
        n = ingest_call_file(f, settings, embedder, writer)
        total += n
        print(f"[{i}/{len(files)}] {Path(f).stem}  ->  {n} rows")
    print(f"done: {total} rows across {len(files)} calls into "
          f"{settings.vdb.bucket}/{settings.vdb.schema_name}.{settings.vdb.table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
