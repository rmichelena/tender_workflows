"""Contexto FEED (descubrimiento) del split feed/pipeline.

Ver `docs/INGEST_CONTRACT.md` §3 y §9. Seam introducido en el paso 0.3a: hoy el feed
se materializa sobre la tabla `processes` (`Process`); este paquete centraliza el acceso
para que scanners y vistas no consulten el ORM directamente y se pueda evolucionar a un
`FeedItem`/`PipelineItem` separado sin tocar a los clientes.
"""

from .repository import FeedRepository

__all__ = ["FeedRepository"]
