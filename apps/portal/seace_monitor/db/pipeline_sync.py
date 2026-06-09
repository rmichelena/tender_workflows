"""Dual-write: sincroniza FeedItem promovido → PipelineItem (0.3e-2).

Durante la fase de dual-write, toda escritura a un item promovido debe reflejarse
en `pipeline_items`. Este módulo centraliza la copia para evitar duplicar lógica
en cada punto de escritura.

El helper `sync_to_pipeline` se llama tras cualquier modificación a un `FeedItem`
promovido (antes del commit). Es idempotente y crea/actualiza el `PipelineItem`
correspondiente.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import inspect as sa_inspect

from .models import AnalysisResult, PipelineItem, FeedItem

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Campos que se copian de FeedItem → PipelineItem.
# Excluimos: id, promoted_at (ya seteado en PipelineItem), source/source_ref
# (van como origin_*), auto_reject_*, promoted_at, list_hash, first_seen_at.
SYNC_FIELDS: tuple[str, ...] = (
    "entity_id",
    "anio",
    "status",
    "workflow_profile",
    "interest_status",
    "lifecycle_phase",
    "nid_proceso",
    "nid_convocatoria",
    "nid_sistema",
    "link_id",
    "ntipo",
    "ficha_id",
    "numero",
    "fecha_publicacion",
    "nomenclatura",
    "reiniciado_desde",
    "objeto",
    "descripcion",
    "cuantia",
    "moneda",
    "version_seace",
    "fecha_consultas",
    "fecha_presentacion",
    "cronograma_json",
    "documentos_json",
    "ficha_url",
    "content_hash",
    "data_dir",
    "watch_unread",
    "watch_checked_at",
    "watch_cronograma_prev_json",
    "watch_documentos_prev_json",
    "watch_changelog_json",
    "list_rank_descargados",
    "list_rank_analizados",
    "updated_at",
)


def _copy_fields(process: FeedItem, pipeline_item: PipelineItem) -> None:
    """Copia los campos sincronizados de FeedItem → PipelineItem."""
    for field in SYNC_FIELDS:
        setattr(pipeline_item, field, getattr(process, field))


def sync_to_pipeline(session: Session, process: FeedItem, *, tenant_id: str = "default") -> PipelineItem | None:
    """Sincroniza un FeedItem promovido a su PipelineItem correspondiente.

    - Si el proceso no está promovido, no hace nada y devuelve None.
    - Si el PipelineItem no existe, lo crea (promoción lazy).
    - Si existe, actualiza los campos sincronizados.
    - Devuelve el PipelineItem (o None).

    No hace commit (responsabilidad del caller).
    """
    if process.promoted_at is None:
        return None

    pi = (
        session.query(PipelineItem)
        .filter(PipelineItem.origin_feed_id == process.id)
        .one_or_none()
    )

    if pi is None:
        pi = PipelineItem(
            origin_feed_id=process.id,
            origin_source=process.source or "seace",
            origin_source_ref=process.source_ref,
            tenant_id="default",
            promoted_at=process.promoted_at,
            first_seen_at=process.first_seen_at,
        )
        session.add(pi)
        logger.debug("Created PipelineItem for FeedItem id=%s", process.id)

    _copy_fields(process, pi)
    return pi


def sync_analysis_to_pipeline(session: Session, process: FeedItem) -> None:
    """Si el FeedItem tiene análisis, sincroniza el FK al PipelineItem.

    Llamar después de `sync_to_pipeline` si el análisis puede haber cambiado.
    """
    if process.promoted_at is None or process.id is None:
        return
    pi = (
        session.query(PipelineItem)
        .filter(PipelineItem.origin_feed_id == process.id)
        .one_or_none()
    )
    if pi is None:
        return
    # Find AnalysisResult by process_id and link to pipeline_item
    ar = (
        session.query(AnalysisResult)
        .filter(AnalysisResult.process_id == process.id)
        .one_or_none()
    )
    if ar is not None and ar.pipeline_item_id is None:
        ar.pipeline_item_id = pi.id
