"""Acceso al feed de descubrimiento (seam del split feed/pipeline, paso 0.3a).

El feed es el firehose ruidoso de items descubiertos por los adapters (SEACE, ADP, …).
Conceptualmente es `FeedItem` (`docs/INGEST_CONTRACT.md` §3), pero hoy se materializa
sobre la tabla `processes`/`Process`. Centralizar el acceso aquí permite:

- que scanners y list views no enramen consultas crudas al ORM, y
- migrar a una tabla `feed_items` separada (paso 0.3e) sin tocar a los clientes.

Este paso es **behavior-preserving**: las consultas son las mismas que estaban inline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ..db.models import Process, ProcessStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session


class FeedRepository:
    """Acceso al feed compartido, materializado sobre `processes` por ahora."""

    def __init__(self, session: "Session") -> None:
        self.session = session

    def find_by_ref(
        self, source: str, entity_id: int, source_ref: str | None
    ) -> Process | None:
        """Item del feed por identidad de fuente ``(source, entity_id, source_ref)``.

        Es el dedupe del descubrimiento: el scanner lo usa para decidir alta vs update.
        """
        return (
            self.session.query(Process)
            .filter(
                Process.source == source,
                Process.entity_id == entity_id,
                Process.source_ref == source_ref,
            )
            .one_or_none()
        )

    def query_by_status(
        self, statuses: Iterable[ProcessStatus], *, source: str | None = None
    ) -> "Query":
        """Query base de items del feed en los estados dados (opcional: por fuente).

        Devuelve un `Query` para que el caller añada `options()`/orden según su vista.
        """
        query = self.session.query(Process).filter(
            Process.status.in_(list(statuses))
        )
        if source is not None:
            query = query.filter(Process.source == source)
        return query

    def claimed_for_entity(
        self,
        source: str,
        entity_id: int,
        statuses: Iterable[ProcessStatus],
    ) -> list[Process]:
        """Items "reclamados" (descargados/analizados/…) de una entidad y fuente.

        Sirve para reconciliar re-publicaciones por UID de negocio (nomenclatura) sin
        depender del `source_ref`/nid, que SEACE reasigna al re-publicar un proceso.
        """
        return (
            self.session.query(Process)
            .filter(
                Process.source == source,
                Process.entity_id == entity_id,
                Process.status.in_(list(statuses)),
            )
            .all()
        )
