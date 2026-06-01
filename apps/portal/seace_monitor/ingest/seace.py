"""Adapter de ingesta SEACE.

Declara capacidades y presentación UI. El scanner/watchlist/descarga siguen viviendo
en módulos propios (`scanner.py`, `watchlist.py`, `analysis/runner.py`); su colapso al
contrato es trabajo de fases posteriores (ver `docs/INGEST_CONTRACT.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import IngestCapabilities

if TYPE_CHECKING:
    from ..db.models import Process


@dataclass(frozen=True)
class SeaceIngestAdapter:
    source: str = "seace"
    label: str = "SEACE"
    view_label: str = "SEACE"
    portal_url: str | None = None  # SEACE se abre vía proxy interno, no URL absoluta.
    capabilities: IngestCapabilities = IngestCapabilities(
        scan_listings=True,
        fetch_by_reference=True,
    )

    def can_open(self, process: "Process") -> bool:
        from ..seace_search import normalize_nomenclatura

        return bool(process.entity and normalize_nomenclatura(process.nomenclatura))


SEACE_ADAPTER = SeaceIngestAdapter()
