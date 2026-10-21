"""ADBC cosine search over the ingested earnings-call corpus."""

from __future__ import annotations

import argparse
from typing import Any

from temp_ingest.config import Settings
from temp_ingest.embeddings import NimEmbedder


def _connect(settings: Settings) -> Any:
    import adbc_driver_manager.dbapi

    return adbc_driver_manager.dbapi.connect(
        driver=settings.vdb.adbc_driver_path,
        db_kwargs={
            "vast.db.endpoint": settings.vdb.endpoint,
            "vast.db.access_key": settings.vast.access_key,
            "vast.db.secret_key": settings.vast.secret_key,
        },
    )


def search(
    settings: Settings,
    query: str,
    top_k: int = 8,
    engagement_id: str | None = None,
    speaker_type: str | None = None,
    customer: str | None = None,
    opportunity: str | None = None,
    sales_stage: str | None = None,
) -> list[dict]:
    embedder = NimEmbedder(settings.nim)
    dim = settings.nim.embed_dim
    vec = embedder.embed([query], input_type="query")[0]
    vec_literal = ",".join(repr(float(x)) for x in vec)

    table_ref = f'"{settings.vdb.bucket}/{settings.vdb.schema_name}"."{settings.vdb.table}"'
    where = ["department = " + _q(settings.ingest.department)]
    if engagement_id:
        where.append("engagement_id = " + _q(engagement_id))
    if speaker_type:
        where.append("speaker_type = " + _q(speaker_type))
    if customer:
        where.append("LOWER(customer) = " + _q(customer.lower()))
    if opportunity:
        where.append("LOWER(opportunity) = " + _q(opportunity.lower()))
    if sales_stage:
        where.append("LOWER(sales_stage) = " + _q(sales_stage.lower()))
    where_sql = " WHERE " + " AND ".join(where)

    sql = (
        "SELECT chunk_id, engagement_id, customer, opportunity, sales_stage, "
        "speaker_name, speaker_type, time_start, text, "
        f"array_cosine_distance(embedding::FLOAT[{dim}], ARRAY[{vec_literal}]::FLOAT[{dim}]) AS distance "
        f"FROM {table_ref}{where_sql} "
        f"ORDER BY distance ASC LIMIT {int(top_k)}"
    )

    conn = _connect(settings)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            names = [c[0] for c in cur.description]
            rows = cur.fetchall()
        finally:
            cur.close()
        return [dict(zip(names, row)) for row in rows]
    finally:
        conn.close()


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    settings = Settings.load()
    parser = argparse.ArgumentParser(prog="temp_ingest.search")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--engagement-id", default=None)
    parser.add_argument("--speaker-type", default=None, choices=[None, "rep", "customer"])
    parser.add_argument("--customer", default=None, help="filter to one customer (account name)")
    parser.add_argument("--opportunity", default=None, help="filter to one opportunity name")
    parser.add_argument("--sales-stage", default=None, help="filter to one sector (sales_stage)")
    args = parser.parse_args()

    hits = search(
        settings,
        args.query,
        args.top_k,
        args.engagement_id,
        args.speaker_type,
        args.customer,
        args.opportunity,
        args.sales_stage,
    )
    print(f'query: "{args.query}"  ({len(hits)} hits)\n')
    for i, h in enumerate(hits, 1):
        snippet = (h["text"] or "")[:180].replace("\n", " ")
        biz = " · ".join(
            x for x in [h.get("customer"), h.get("opportunity"), h.get("sales_stage")] if x
        )
        print(
            f"{i:2d}. dist={h['distance']:.4f}  [{h['speaker_type']}/{h['speaker_name']}] "
            f"@{h['time_start']:.0f}s  call={h['engagement_id'][:8]}…\n"
            f"    {biz}\n    {snippet}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
