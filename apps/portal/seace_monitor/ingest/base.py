"""Tipos base para canales de ingesta (contrato de plugin).

Estado de implementación (ver `docs/INGEST_CONTRACT.md`):

- **Fase 0.1 (este código):** identidad (`source`/`label`) + presentación en UI
  (`can_open`/`view_label`/`portal_url`). El registry deja de ser solo metadatos: la
  UI ya consume el adapter en vez de ramificar por `source`.
- **Fases siguientes:** `discover()`, `detect_changes()`, `fetch_document_index()`,
  `download_documents()` — colapsarán los módulos `*_scanner`/`*_watchlist`/`*_downloader`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..db.models import Process


class UnknownIngestSource(ValueError):
    def __init__(self, source: str) -> None:
        super().__init__(f"Canal de ingesta no registrado: {source!r}")


@dataclass(frozen=True)
class IngestCapabilities:
    scan_listings: bool = False
    fetch_by_reference: bool = False
    create_from_upload: bool = False
    # El item se abre en un portal externo absoluto (p. ej. ADP) en vez de un proxy interno (SEACE).
    opens_external_portal: bool = False


@runtime_checkable
class SourceAdapter(Protocol):
    """Contrato de comportamiento de un canal de ingesta.

    Solo se declaran aquí los miembros ya implementados (Fase 0.1). Los métodos de
    descubrimiento/descarga se añadirán al colapsar los módulos por fuente.
    """

    source: str
    label: str
    # Texto para el botón "Ver en {view_label}" (SEACE, ADP, …).
    view_label: str
    # Landing absoluto del portal cuando `capabilities.opens_external_portal` es True; si no, None.
    portal_url: str | None
    capabilities: IngestCapabilities

    def can_open(self, process: "Process") -> bool:
        """¿Se puede abrir la ficha origen de este proceso desde la UI?"""
        ...


# Alias de compatibilidad (nombre histórico).
IngestAdapter = SourceAdapter
