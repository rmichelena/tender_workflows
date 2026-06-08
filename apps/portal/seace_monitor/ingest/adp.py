"""Adapter de ingesta ADP Portal (Aeropuertos del Perú).

Declara capacidades y presentación UI. El scanner/watchlist/descarga viven en
`adp_scanner.py`, `adp_watchlist.py`, `adp_downloader.py`; su colapso al contrato es
trabajo de fases posteriores (ver `docs/INGEST_CONTRACT.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .base import IngestCapabilities

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..analysis.runner import AnalysisRunner
    from ..config import AppConfig
    from ..db.models import Process

ADP_PORTAL_URL = "https://www.adp.com.pe/"


@dataclass(frozen=True)
class AdpIngestAdapter:
    source: str = "adp_portal"
    label: str = "ADP Portal"
    view_label: str = "ADP"
    portal_url: str | None = ADP_PORTAL_URL  # ADP no tiene deep-link por item.
    scan_priority: int = 10  # tras SEACE.
    capabilities: IngestCapabilities = IngestCapabilities(
        scan_listings=True,
        fetch_by_reference=False,
        opens_external_portal=True,
    )

    def can_open(self, process: "Process") -> bool:
        return bool(process.source_ref)

    def resolve_document_index(
        self, runner: "AnalysisRunner", process: "Process"
    ) -> list[dict]:
        # ADP ya tiene los documentos parseados en documentos_json (los puso el scanner).
        return runner._fetch_documentos_from_adp(process)

    def fetch_documents(
        self, runner: "AnalysisRunner", docs: list[dict], docs_dir: Path
    ) -> None:
        # ADP descarga vía HTTP directo; no hay archivos comprimidos que extraer.
        runner._fetch_adp_documents(docs, docs_dir)

    def scan_enabled(self, config: "AppConfig") -> bool:
        return config.adp.enabled

    def scan_interval_seconds(self, config: "AppConfig") -> int:
        return config.adp.poll_interval_seconds

    def watch_interval_seconds(self, config: "AppConfig") -> int:
        return config.watchlist_worker_wake_seconds

    def scan(self, config: "AppConfig", session: "Session") -> int:
        from ..adp_scanner import AdpScanner

        return AdpScanner(config, session).run_once()

    def refresh_watchlist(self, config: "AppConfig", session: "Session") -> int:
        from ..adp_watchlist import refresh_adp_watchlist

        return refresh_adp_watchlist(config, session)


ADP_ADAPTER = AdpIngestAdapter()
