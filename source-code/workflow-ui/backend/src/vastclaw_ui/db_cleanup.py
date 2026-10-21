"""Drop vastclaw-db tables (deploy teardown helper)."""

from __future__ import annotations

import logging
import sys
from typing import Any

from vastclaw_ui.config import Settings

_log = logging.getLogger("vastclaw.db_cleanup")

MEMORY_SCHEMAS = ("memory_sales", "memory_finance", "memory")


def _drop_tables(
    session: Any,
    bucket: str,
    schema_name: str,
    table_names: list[str] | None = None,
) -> None:
    with session.transaction() as tx:
        schema = tx.bucket(bucket).schema(schema_name, fail_if_missing=False)
        if schema is None:
            _log.info("VastDB: schema not found, skip %s/%s", bucket, schema_name)
            return

        names = table_names or schema.tablenames()
        if not names:
            _log.info("VastDB: no tables in %s/%s", bucket, schema_name)
            return

        for name in names:
            table = schema.table(name, fail_if_missing=False)
            if table is None:
                _log.warning("VastDB: table not found, skip %s/%s/%s", bucket, schema_name, name)
                continue
            _log.info("VastDB: dropping %s/%s/%s", bucket, schema_name, name)
            table.drop()
            _log.info("VastDB: dropped %s", name)


def wipe_vastclaw_db(settings: Settings) -> None:
    import vastdb

    bucket = settings.vdb.control_bucket  # vastclaw-db
    session = vastdb.connect(
        endpoint=settings.vdb.endpoint,
        access=settings.vast.access_key,
        secret=settings.vast.secret_key,
        ssl_verify=settings.vdb.ssl_verify,
    )

    _log.info("VastDB cleanup: bucket=%s endpoint=%s", bucket, settings.vdb.endpoint)

    _drop_tables(session, bucket, settings.vdb.telemetry_schema)
    _drop_tables(session, bucket, settings.vdb.control_schema)
    for schema_name in MEMORY_SCHEMAS:
        _drop_tables(session, bucket, schema_name)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_yaml()
    try:
        wipe_vastclaw_db(settings)
    except Exception as exc:
        _log.error("vastclaw-db cleanup failed: %s", exc)
        return 1
    _log.info("vastclaw-db cleanup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
