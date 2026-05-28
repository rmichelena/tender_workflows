"""Tipos base para canales de ingesta."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class UnknownIngestSource(ValueError):
    def __init__(self, source: str) -> None:
        super().__init__(f"Canal de ingesta no registrado: {source!r}")


@dataclass(frozen=True)
class IngestCapabilities:
    scan_listings: bool = False
    fetch_by_reference: bool = False
    create_from_upload: bool = False


class IngestAdapter(Protocol):
    source: str
    label: str
    capabilities: IngestCapabilities
