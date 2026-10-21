#!/usr/bin/env python3
"""End-to-end: Earnings-21 → NGC nv-embedqa-e5-v5 → VastDB."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from temp_ingest.config import Settings
from temp_ingest.embeddings import NimEmbedder
from temp_ingest.ingest import ingest_call_file
from temp_ingest.prepare_earnings21 import download_earnings21, prepare
from temp_ingest.vastdb_writer import VastDBWriter


def _print_banner(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _require_secrets(settings: Settings, *, need_nim: bool, need_vast: bool) -> None:
    errors: list[str] = []
    if need_nim and settings.nim.api_key and "REPLACE_ME" in settings.nim.api_key:
        errors.append("NIM_API_KEY / nim.api_key missing (NGC key for nv-embedqa-e5-v5)")
    if need_vast:
        if not settings.vast.access_key or "REPLACE_ME" in settings.vast.access_key:
            errors.append("VAST_ACCESS_KEY / vast.access_key missing")
        if not settings.vast.secret_key or "REPLACE_ME" in settings.vast.secret_key:
            errors.append("VAST_SECRET_KEY / vast.secret_key missing")
    if errors:
        print("ERROR: missing credentials:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "Set them in config/config.yaml or via env (see config/config.example.yaml).",
            file=sys.stderr,
        )
        raise SystemExit(2)


def main() -> int:
    settings = Settings.load()
    parser = argparse.ArgumentParser(
        prog="temp_ingest.run_earnings21_e2e",
        description="Download Earnings-21, embed with NGC nv-embedqa-e5-v5, ingest to VastDB",
    )
    parser.add_argument(
        "--download-dir",
        default="data/raw",
        help="sparse-clone root (default: data/raw)",
    )
    parser.add_argument(
        "--data-dir",
        default=settings.ingest.data_dir,
        help="per-call parquet output / ingest input (default from config)",
    )
    parser.add_argument(
        "--eval10",
        action="store_true",
        help="only Eval-10 subset (~10 hours)",
    )
    parser.add_argument("--limit", type=int, default=0, help="cap calls at prepare time; 0=all")
    parser.add_argument(
        "--max-calls",
        type=int,
        default=0,
        help="cap calls at ingest time; 0=all prepared parquet files",
    )
    parser.add_argument(
        "--target-words",
        type=int,
        default=180,
        help="chunk size target (words) when merging same-speaker turns",
    )
    parser.add_argument("--max-seconds", type=float, default=90.0)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse existing sparse clone under --download-dir",
    )
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument(
        "--skip-prepare",
        action="store_true",
        help="skip download/convert; ingest existing parquet in --data-dir",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="stop after writing parquet (no embed / no VastDB)",
    )
    parser.add_argument(
        "--smoke-query",
        default="",
        help="after ingest, run one vector search with this query (needs ADBC driver)",
    )
    parser.add_argument(
        "--department",
        default="",
        help="override ingest.department ACL column (default from config)",
    )
    args = parser.parse_args()

    if args.department:
        settings.ingest.department = args.department
    settings.ingest.data_dir = args.data_dir
    data_dir = Path(args.data_dir)

    # ---- 1+2 prepare --------------------------------------------------------
    if not args.skip_prepare:
        _print_banner("1/3  Download Earnings-21 transcripts (no audio)")
        download_dir = Path(args.download_dir)
        if args.skip_download:
            earnings21 = download_dir / "speech-datasets" / "earnings21"
            if not earnings21.exists():
                print(f"ERROR: {earnings21} not found", file=sys.stderr)
                return 1
        else:
            earnings21 = download_earnings21(download_dir, force=args.force_download)

        _print_banner("2/3  Convert .nlp → per-call parquet")
        written = prepare(
            earnings21,
            data_dir,
            eval10=args.eval10,
            limit=args.limit,
            target_words=args.target_words,
            max_seconds=args.max_seconds,
        )
        if not written:
            print("ERROR: no parquet files produced", file=sys.stderr)
            return 1
        print(f"[e2e] prepared {len(written)} call parquet file(s) under {data_dir}")
    else:
        print(f"[e2e] skip prepare — using parquet in {data_dir}")

    if args.prepare_only:
        print("[e2e] --prepare-only set; done.")
        return 0

    # ---- 3 embed + VastDB ---------------------------------------------------
    _require_secrets(settings, need_nim=True, need_vast=True)
    _print_banner(
        f"3/3  Embed (NGC {settings.nim.embed_model}, dim={settings.nim.embed_dim}) "
        f"→ VastDB {settings.vdb.bucket}/{settings.vdb.schema_name}.{settings.vdb.table}"
    )
    print(f"[e2e] NIM base_url = {settings.nim.base_url}")
    print(f"[e2e] VastDB endpoint = {settings.vdb.endpoint}")
    print(f"[e2e] department ACL  = {settings.ingest.department}")

    files = sorted(data_dir.glob("*.parquet"))
    if not files:
        print(f"ERROR: no parquet in {data_dir}", file=sys.stderr)
        return 1
    if args.max_calls and args.max_calls > 0:
        files = files[: args.max_calls]
        print(f"[e2e] limiting ingest to {len(files)} call(s)")

    embedder = NimEmbedder(settings.nim)
    writer = VastDBWriter(settings)
    writer.ensure_table()

    total_rows = 0
    for i, path in enumerate(files, 1):
        n = ingest_call_file(path, settings, embedder, writer)
        total_rows += n
        print(f"[ingest] [{i}/{len(files)}] {path.stem} -> {n} rows")

    print(
        f"[e2e] done: {total_rows} chunks across {len(files)} calls "
        f"into {settings.vdb.bucket}/{settings.vdb.schema_name}.{settings.vdb.table}"
    )

    # ---- 4 optional smoke search --------------------------------------------
    if args.smoke_query:
        _print_banner("Smoke search (query embed + VastDB cosine)")
        try:
            from temp_ingest.search import search

            hits = search(settings, args.smoke_query, top_k=5)
        except Exception as exc:  # noqa: BLE001 — surface driver/config issues clearly
            print(f"[e2e] smoke search failed (ingest still OK): {exc}", file=sys.stderr)
            print(
                "[e2e] Tip: set vdb.adbc_driver_path in config, or run "
                "`python -m temp_ingest.search --query '...'` later.",
                file=sys.stderr,
            )
            return 0
        if not hits:
            print("[e2e] smoke search returned 0 hits — check department / table")
            return 0
        for rank, hit in enumerate(hits, 1):
            text = (hit.get("text") or "").replace("\n", " ")
            preview = text[:160] + ("…" if len(text) > 160 else "")
            print(
                f"  #{rank} dist={hit.get('distance'):.4f}  "
                f"{hit.get('customer')} / {hit.get('speaker_name')}: {preview}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
