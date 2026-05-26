"""View-models para listas web (sin mutar ORM)."""

from __future__ import annotations

from dataclasses import dataclass

from ..parser import fechas_listado_from_cronograma_json
from .models import Process


@dataclass(frozen=True)
class ProcessListView:
    process: Process
    fin_consultas: str
    fin_presentacion: str
    watch_unread: bool = False


def build_process_list_views(processes: list[Process]) -> list[ProcessListView]:
    views: list[ProcessListView] = []
    for proc in processes:
        fechas = fechas_listado_from_cronograma_json(
            proc.cronograma_json,
            fallback_consultas=proc.fecha_consultas or "",
            fallback_presentacion=proc.fecha_presentacion or "",
        )
        views.append(
            ProcessListView(
                process=proc,
                fin_consultas=fechas.fecha_consultas or "—",
                fin_presentacion=fechas.fecha_presentacion or "—",
                watch_unread=bool(proc.watch_unread),
            )
        )
    return views
