"""Make VastDB SDK `select()` work on tables that contain a vector column."""

from __future__ import annotations

import logging

import pyarrow as pa
import vastdb._internal as _internal

logger = logging.getLogger(__name__)


def is_vector_field(field: pa.Field) -> bool:
    t = str(field.type)
    if "fixed_size_list" in t:
        return True
    return "list<" in t and "float" in t


def _install() -> None:
    if getattr(_internal.build_query_data_request, "_vastclaw_patched", False):
        return

    _original = _internal.build_query_data_request

    def _patched(schema=pa.schema([]), predicate=None, field_names=None):
        supported = [f for f in schema if not is_vector_field(f)]
        unsupported = {f.name for f in schema if is_vector_field(f)}
        if unsupported:
            logger.debug("[vast-claw] excluding vector columns from SDK query: %s", unsupported)
        names = field_names
        if names is not None:
            names = [n for n in names if n not in unsupported]
        return _original(pa.schema(supported), predicate, names)

    _patched._vastclaw_patched = True  # type: ignore[attr-defined]
    _internal.build_query_data_request = _patched


_install()
