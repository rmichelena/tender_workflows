"""Registry de adapters de ingesta."""

from __future__ import annotations

from .base import IngestAdapter, IngestCapabilities, UnknownIngestSource
from .seace import SEACE_ADAPTER

_ADAPTERS: dict[str, IngestAdapter] = {
    SEACE_ADAPTER.source: SEACE_ADAPTER,
}


def get_adapter(source: str) -> IngestAdapter:
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
    "UnknownIngestSource",
    "get_adapter",
    "registered_sources",
]
