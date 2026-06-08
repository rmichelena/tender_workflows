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
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..analysis.runner import AnalysisRunner
    from ..config import AppConfig
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
    # Orden de ejecución en el worker (menor = primero). Preserva fronteras transaccionales
    # entre fuentes (p. ej. SEACE commitea por entidad).
    scan_priority: int
    capabilities: IngestCapabilities

    def can_open(self, process: "Process") -> bool:
        """¿Se puede abrir la ficha origen de este proceso desde la UI?"""
        ...

    # --- Worker: descubrimiento y watchlist (Fase 0.3) -------------------
    # El worker es agnóstico a la fuente: itera adapters y delega cada ciclo
    # aquí. Transicional: hoy delegan en scanner/watchlist existentes; el modelo
    # objetivo discover()/detect_changes() por item llega con feed/pipeline.

    def scan_enabled(self, config: "AppConfig") -> bool:
        """¿Está habilitado el escaneo de esta fuente para esta config?"""
        ...

    def scan_interval_seconds(self, config: "AppConfig") -> int:
        """Periodo entre escaneos de listado."""
        ...

    def watch_interval_seconds(self, config: "AppConfig") -> int:
        """Periodo entre ciclos de watchlist en el worker (mínimo TTL base/urgente)."""
        ...

    def scan(self, config: "AppConfig", session: "Session") -> int:
        """Ejecuta un ciclo de escaneo; devuelve nº de items nuevos."""
        ...

    def refresh_watchlist(self, config: "AppConfig", session: "Session") -> int:
        """Ejecuta un ciclo de watchlist; devuelve nº de items actualizados."""
        ...

    # --- Descarga (Fase 0.2) ---------------------------------------------
    # Dispatch de descarga por fuente. El orquestador (AnalysisRunner) maneja
    # sesión/commits/cleanup; el adapter encapsula los pasos específicos de la
    # fuente. Transicional: hoy delegan en métodos del runner; el código pesado
    # migrará al adapter/módulos compartidos en una sub-fase posterior.

    def resolve_document_index(
        self, runner: "AnalysisRunner", process: "Process"
    ) -> list[dict]:
        """Resuelve la lista de documentos a descargar (puede mutar `process`).

        Se ejecuta fuera del bloque de cleanup; su salida se persiste como
        `documentos_json` tras la descarga.
        """
        ...

    def fetch_documents(
        self, runner: "AnalysisRunner", docs: list[dict], docs_dir: Path
    ) -> None:
        """Descarga los bytes a `docs_dir` (incluye extracción cuando aplique).

        Se ejecuta dentro del bloque protegido: si lanza, el runner limpia el
        directorio y revierte el estado.
        """
        ...


# Alias de compatibilidad (nombre histórico).
IngestAdapter = SourceAdapter
