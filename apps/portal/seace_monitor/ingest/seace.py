"""Adapter de ingesta SEACE.

Declara capacidades y presentación UI. El scanner/watchlist/descarga siguen viviendo
en módulos propios (`scanner.py`, `watchlist.py`, `analysis/runner.py`); su colapso al
contrato es trabajo de fases posteriores (ver `docs/INGEST_CONTRACT.md`).
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


@dataclass(frozen=True)
class SeaceIngestAdapter:
    source: str = "seace"
    label: str = "SEACE"
    view_label: str = "SEACE"
    portal_url: str | None = None  # SEACE se abre vía proxy interno, no URL absoluta.
    scan_priority: int = 0  # SEACE primero (commitea por entidad).
    capabilities: IngestCapabilities = IngestCapabilities(
        scan_listings=True,
        fetch_by_reference=True,
    )

    def can_open(self, process: "Process") -> bool:
        from ..seace_search import normalize_nomenclatura

        return bool(process.entity and normalize_nomenclatura(process.nomenclatura))

    def resolve_document_index(
        self, runner: "AnalysisRunner", process: "Process"
    ) -> list[dict]:
        # Abre la ficha SEACE en vivo, refresca metadatos del proceso y devuelve docs.
        return runner._fetch_documentos_from_seace(process, process.entity.ruc)

    def fetch_documents(
        self, runner: "AnalysisRunner", docs: list[dict], docs_dir: Path
    ) -> None:
        from ..analysis.document_prep import extract_archives

        runner._fetch_documents(docs, docs_dir)
        extract_archives(docs_dir)

    def scan_enabled(self, config: "AppConfig") -> bool:
        return True

    def scan_interval_seconds(self, config: "AppConfig") -> int:
        return config.poll_interval_seconds

    def watch_interval_seconds(self, config: "AppConfig") -> int:
        return config.watchlist_refresh_seconds

    def scan(self, config: "AppConfig", session: "Session") -> int:
        from ..scanner import MultiEntityScanner

        return MultiEntityScanner(config, session).run_once()

    def refresh_watchlist(self, config: "AppConfig", session: "Session") -> int:
        from ..watchlist import refresh_watchlist_processes

        return refresh_watchlist_processes(config, session)


SEACE_ADAPTER = SeaceIngestAdapter()
