"""Contexto FEED (descubrimiento) del split feed/pipeline.

Ver `docs/INGEST_CONTRACT.md` §3 y §9. Seam introducido en el paso 0.3a: hoy el feed
se materializa sobre la tabla `processes` (`Process`); este paquete centraliza el acceso
para que scanners y vistas no consulten el ORM directamente y se pueda evolucionar a un
`FeedItem`/`PipelineItem` separado sin tocar a los clientes.
"""

from .decisions import (
    DEFAULT_TENANT_ID,
    clear_all_feed_decisions,
    clear_feed_decision,
    record_autoreject_decision,
    record_exempt_decision,
)
from .promotion import is_promoted, promote, should_be_promoted
from .repository import FeedRepository

__all__ = [
    "FeedRepository",
    "DEFAULT_TENANT_ID",
    "clear_all_feed_decisions",
    "clear_feed_decision",
    "record_autoreject_decision",
    "record_exempt_decision",
    "is_promoted",
    "promote",
    "should_be_promoted",
]
