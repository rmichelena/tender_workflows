"""Adapter de ingesta ADP Portal (Aeropuertos del Perú).

Declara capacidades y presentación UI. El scanner/watchlist/descarga viven en
`adp_scanner.py`, `adp_watchlist.py`, `adp_downloader.py`; su colapso al contrato es
trabajo de fases posteriores (ver `docs/INGEST_CONTRACT.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .base import IngestCapabilities

if TYPE_CHECKING:
    from ..db.models import Process

ADP_PORTAL_URL = "https://www.adp.com.pe/"


@dataclass(frozen=True)
class AdpIngestAdapter:
    source: str = "adp_portal"
    label: str = "ADP Portal"
    view_label: str = "ADP"
    portal_url: str | None = ADP_PORTAL_URL  # ADP no tiene deep-link por item.
    capabilities: IngestCapabilities = IngestCapabilities(
        scan_listings=True,
        fetch_by_reference=False,
        opens_external_portal=True,
    )

    def can_open(self, process: "Process") -> bool:
        return bool(process.source_ref)


ADP_ADAPTER = AdpIngestAdapter()
