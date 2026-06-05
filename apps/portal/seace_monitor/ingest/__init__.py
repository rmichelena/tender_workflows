"""Registry de adapters de ingesta."""

from __future__ import annotations

from .base import IngestAdapter, IngestCapabilities, SourceAdapter, UnknownIngestSource
from .seace import SEACE_ADAPTER
from .adp import ADP_ADAPTER

_ADAPTERS: dict[str, SourceAdapter] = {
    SEACE_ADAPTER.source: SEACE_ADAPTER,
    ADP_ADAPTER.source: ADP_ADAPTER,
}


def get_adapter(source: str) -> SourceAdapter:
    key = (source or "").strip().lower()
    try:
        return _ADAPTERS[key]
    except KeyError as exc:
        raise UnknownIngestSource(source) from exc


def registered_sources() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


__all__ = [
    "IngestAdapter",
    "IngestCapabilities",
    "SourceAdapter",
    "UnknownIngestSource",
    "get_adapter",
    "registered_sources",
]
