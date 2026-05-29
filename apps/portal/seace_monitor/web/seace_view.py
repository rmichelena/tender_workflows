"""Helpers para abrir fichas de procesos desde la UI (SEACE y ADP)."""

from __future__ import annotations

from ..db.models import Process
from ..seace_search import normalize_nomenclatura

ADP_PORTAL_URL = "https://www.adp.com.pe/"


def can_open_seace(process: Process) -> bool:
    return bool(
        process.source != "adp_portal"
        and process.entity
        and normalize_nomenclatura(process.nomenclatura)
    )


def can_open_source(process: Process) -> bool:
    """True si se puede abrir la ficha origen del proceso en un navegador."""
    if process.source == "adp_portal":
        return bool(process.source_ref)
    return can_open_seace(process)


def source_button_label(process: Process) -> str:
    """Texto del botón según el source."""
    if process.source == "adp_portal":
        return "Ver en ADP"
    return "Ver en SEACE"


def source_view_url(process) -> str:
    """URL destino del botón según el source."""
    if process.source == "adp_portal":
        # ADP no tiene páginas individuales; placeholder al portal
        return ADP_PORTAL_URL
    from .seace_proxy import seace_view_path
    return seace_view_path(process.id)
