"""Adapter de ingesta SEACE.

Por ahora declara capacidades y mantiene el scanner existente como implementación
operativa; futuros adapters deben exponer el mismo contrato de registry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import IngestCapabilities


@dataclass(frozen=True)
class SeaceIngestAdapter:
    source: str = "seace"
    label: str = "SEACE"
    capabilities: IngestCapabilities = IngestCapabilities(
        scan_listings=True,
        fetch_by_reference=True,
    )


SEACE_ADAPTER = SeaceIngestAdapter()
