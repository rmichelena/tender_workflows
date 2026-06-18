"""Acceso al pipeline de trabajo curado (0.3e-3 + 0.3f).

El pipeline contiene los items que el usuario ha reclamado (descargado, analizado,
archivado, portafolio). Lecturas y escrituras pipeline operan sobre `pipeline_items`.

Incluye helper `get_pipeline_item_by_feed_id` para lookup por FeedItem.id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ..db.models import PipelineItem, ProcessStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Query, Session


class PipelineRepository:
    """Acceso al pipeline privado por tenant, materializado sobre `pipeline_items`."""

    def __init__(self, session: "Session", tenant_id: str = "default") -> None:
        self.session = session
        self.tenant_id = tenant_id

    def get(self, pipeline_item_id: int) -> PipelineItem | None:
        """Obtiene un PipelineItem por ID (solo del tenant actual)."""
        return (
            self.session.query(PipelineItem)
            .filter(
                PipelineItem.id == pipeline_item_id,
                PipelineItem.tenant_id == self.tenant_id,
            )
            .one_or_none()
        )

    def get_by_origin_feed_id(self, feed_id: int) -> PipelineItem | None:
        """Busca el PipelineItem que proviene de un feed item dado."""
        return (
            self.session.query(PipelineItem)
            .filter(
                PipelineItem.origin_feed_id == feed_id,
                PipelineItem.tenant_id == self.tenant_id,
            )
            .one_or_none()
        )

    def query_by_status(
        self,
        statuses: Iterable[ProcessStatus],
    ) -> "Query":
        """Query base de items del pipeline en los estados dados."""
        return (
            self.session.query(PipelineItem)
            .filter(
                PipelineItem.status.in_(list(statuses)),
                PipelineItem.tenant_id == self.tenant_id,
            )
        )

    def all_pipeline_statuses(self) -> "Query":
        """Todos los items en estados de pipeline (descargada→archivada)."""
        pipeline_statuses = [
            ProcessStatus.descargando,
            ProcessStatus.descargada,
            ProcessStatus.descartando,
            ProcessStatus.analizada,
            ProcessStatus.portafolio,
            ProcessStatus.archivando,
            ProcessStatus.archivada,
        ]
        return self.query_by_status(pipeline_statuses)


def get_pipeline_item_by_feed_id(
    session: "Session", feed_item_id: int
) -> PipelineItem | None:
    """Busca el PipelineItem correspondiente a un FeedItem por origin_feed_id."""
    return (
        session.query(PipelineItem)
        .filter(PipelineItem.origin_feed_id == feed_item_id)
        .one_or_none()
    )
