# Earnings-21 → NGC embedqa → VastDB

End-to-end ingest for the public [Earnings-21](https://github.com/revdotcom/speech-datasets)
corpus into the VastDB table sales + finance MCP tools retrieve from
(`earnings-calls` / `earnings21` / `call_chunks`).

## What it does

1. Sparse-clones **transcripts + metadata only** (no multi-GB audio)
2. Reconstructs speaker turns from token-level `.nlp` files and packs them into
   ~180-word chunks
3. Embeds each chunk with hosted NGC **`nvidia/nv-embedqa-e5-v5`**
   (`input_type=passage`, dim **1024**)
4. Upserts into VastDB (`bucket/schema.table` from config) — idempotent per call
5. Optional smoke vector search (`input_type=query`)

## Setup

```bash
cd source-code/ingest
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp config/config.example.yaml config/config.yaml
# edit config.yaml — OR export:
export NIM_API_KEY=nvapi-...
export VAST_ACCESS_KEY=...
export VAST_SECRET_KEY=...
export VASTDB_ENDPOINT=http://<vastdb-vip>
```

All 44 Earnings-21 calls are already parsed in `corpus/earnings21/` (one parquet
per call, 3062 chunks). Deploy does not clone anything. With
`platform.corpus_load: true` a cluster pod embeds that whole set and writes it
to VastDB, so every company in the sanity prompts can be cited from the table.
The pod is what reaches the VIP. An empty `nim.api_key` means the embed server
allows anonymous calls, and the client omits the Authorization header.

## Run

```bash
# Full corpus (44 calls, ~39h of speech → a few thousand text chunks)
python -m temp_ingest.run_earnings21_e2e

# Fast demo subset (~10h Eval-10) + prove retrieval
python -m temp_ingest.run_earnings21_e2e --eval10 \
  --smoke-query "What did management say about guidance and margins?"

# Prepare parquet only (no NGC / no VastDB)
python -m temp_ingest.run_earnings21_e2e --prepare-only --limit 3

# Re-ingest after a failed mid-run (reuse clone + parquet)
python -m temp_ingest.run_earnings21_e2e --skip-prepare --max-calls 10
```

## Step-by-step (same pieces, separate)

```bash
python -m temp_ingest.prepare_earnings21 --eval10
python -m temp_ingest.ingest --data-dir data/calls
python -m temp_ingest.search --query "pricing and competitive pressure" --top-k 8
```

Point MCP at the same table via `deploy/config/mcp-{sales,finance}.config.yaml`
(`earnings-calls` / `earnings21` / `call_chunks`), then re-apply secrets:

```bash
./deploy/vast-claw-tools.sh
```

## Demo prompts

Ready-to-paste sales / finance research tasks and Quick Q&A follow-ups:

[`EARNINGS21_PROMPTS.md`](EARNINGS21_PROMPTS.md)

## Notes

- **Do not mix embed models/dims** with an existing corpus. This path is
  `nv-embedqa-e5-v5` / 1024 — same as Claw query embeds.
- Chunks carry `customer` (= company), `opportunity` (= `Company Qn 2020 earnings`),
  `sales_stage` (= sector), `speaker_name` / `speaker_type` (`company`|`analyst`|`operator`), timestamps.
- `department` ACL defaults to `sales` so both sales and finance agents can share
  the corpus (finance MCP sets `rag_department_scope: sales`; persona differs).
- License: Earnings-21 is **CC BY-SA 4.0** — keep attribution if you redistribute.
