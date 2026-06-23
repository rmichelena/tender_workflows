"""View-models para listas web (sin mutar ORM)."""

from __future__ import annotations

from dataclasses import dataclass

from ..parser import fechas_listado_from_cronograma_json
from .models import PipelineItem, FeedItem


@dataclass(frozen=True)
class ProcessListView:
    process: FeedItem | PipelineItem
    fin_consultas: str
    fin_presentacion: str
    watch_unread: bool = False
    correlativo: int | None = None


def build_process_list_views(
    processes: list[FeedItem], *, rank_attr: str | None = None
) -> list[ProcessListView]:
    views: list[ProcessListView] = []
    for proc in processes:
        fechas = fechas_listado_from_cronograma_json(
            proc.cronograma_json,
            fallback_consultas=proc.fecha_consultas or "",
            fallback_presentacion=proc.fecha_presentacion or "",
        )
        correlativo = getattr(proc, rank_attr) if rank_attr else None
        views.append(
            ProcessListView(
                process=proc,
                fin_consultas=fechas.fecha_consultas or "—",
                fin_presentacion=fechas.fecha_presentacion or "—",
                watch_unread=bool(proc.watch_unread),
                correlativo=correlativo,
            )
        )
    return views


def build_pipeline_list_views(
    items: list[PipelineItem],
    *,
    rank_attr: str | None = None,
    watch_unread_by_origin_id: dict[int, bool] | None = None,
) -> list[ProcessListView]:
    """Idéntico a build_process_list_views pero para PipelineItem."""
    views: list[ProcessListView] = []
    for item in items:
        fechas = fechas_listado_from_cronograma_json(
            item.cronograma_json,
            fallback_consultas=item.fecha_consultas or "",
            fallback_presentacion=item.fecha_presentacion or "",
        )
        correlativo = getattr(item, rank_attr) if rank_attr else None
        watch_unread = bool(item.watch_unread)
        if watch_unread_by_origin_id is not None and item.origin_feed_id is not None:
            watch_unread = bool(
                watch_unread_by_origin_id.get(item.origin_feed_id, watch_unread)
            )
        views.append(
            ProcessListView(
                process=item,
                fin_consultas=fechas.fecha_consultas or "—",
                fin_presentacion=fechas.fecha_presentacion or "—",
                watch_unread=watch_unread,
                correlativo=correlativo,
            )
        )
    return views
