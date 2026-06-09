"""Helpers para abrir fichas de procesos desde la UI (delegan en el registry de fuentes).

El conocimiento de cada fuente vive en su adapter (`ingest/`), no en condicionales por
`source` aquí. La resolución de URL interna (proxy SEACE) sí es responsabilidad de la
capa web y se mantiene en este módulo.
"""

from __future__ import annotations

from ..db.models import FeedItem
from ..ingest import get_adapter
from ..ingest.base import SourceAdapter, UnknownIngestSource


def _adapter_for(process: FeedItem) -> SourceAdapter | None:
    try:
        return get_adapter(process.source)
    except UnknownIngestSource:
        return None


def can_open_source(process: FeedItem) -> bool:
    """True si se puede abrir la ficha origen del proceso (cualquier fuente)."""
    adapter = _adapter_for(process)
    return bool(adapter and adapter.can_open(process))


def can_open_seace(process: FeedItem) -> bool:
    """True solo para procesos SEACE abribles (usado por el proxy `/seace/...`).

    Compara contra el `source` canónico del adapter (no `process.source` crudo) para
    quedar alineado con la normalización case-insensitive de `get_adapter`.
    """
    adapter = _adapter_for(process)
    return bool(adapter and adapter.source == "seace" and adapter.can_open(process))


def source_button_label(process: FeedItem) -> str:
    """Texto del botón según la fuente: 'Ver en SEACE', 'Ver en ADP', …"""
    adapter = _adapter_for(process)
    return f"Ver en {adapter.view_label}" if adapter else "Ver en SEACE"


def source_view_url(process: FeedItem) -> str:
    """URL destino del botón: portal externo (dato del adapter) o proxy interno SEACE."""
    adapter = _adapter_for(process)
    if adapter and adapter.portal_url:
        return adapter.portal_url
    from .seace_proxy import seace_view_path

    return seace_view_path(process.id)
