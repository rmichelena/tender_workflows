"""Adapter de ingesta ADP Portal.

Declara capacidades del portal de Aeropuertos del Perú.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import IngestCapabilities


@dataclass(frozen=True)
class AdpIngestAdapter:
    source: str = "adp_portal"
    label: str = "ADP Portal"
    capabilities: IngestCapabilities = IngestCapabilities(
        scan_listings=True,
        fetch_by_reference=False,
    )


ADP_ADAPTER = AdpIngestAdapter()
