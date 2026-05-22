"""Políticas al quitar entidades del escaneo."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Process, ProcessStatus
from .process_storage import archive_analyzed_process, discard_process_downloads
from .scan_options import RemovedEntityPolicy

_ANALYZED_STATUSES = frozenset(
    {ProcessStatus.analizada, ProcessStatus.portafolio, ProcessStatus.archivada}
)
_DOWNLOADED_STATUSES = frozenset({ProcessStatus.descargada, ProcessStatus.descargando})
_PUBLIC_STATUSES = frozenset({ProcessStatus.publicada})


@dataclass(frozen=True)
class EntityProcessCounts:
    publicados: int
    descargados: int
    analizados: int

    @property
    def total(self) -> int:
        return self.publicados + self.descargados + self.analizados


def count_processes_for_entities(
    session: Session, entity_ids: list[int]
) -> EntityProcessCounts:
    if not entity_ids:
        return EntityProcessCounts(0, 0, 0)
    rows = (
        session.query(Process.status, Process.id)
        .filter(Process.entity_id.in_(entity_ids))
        .all()
    )
    publicados = descargados = analizados = 0
    for status, _pid in rows:
        if status in _PUBLIC_STATUSES:
            publicados += 1
        elif status in _DOWNLOADED_STATUSES:
            descargados += 1
        elif status in _ANALYZED_STATUSES:
            analizados += 1
    return EntityProcessCounts(publicados, descargados, analizados)


def apply_removed_entity_policy(
    session: Session,
    config: AppConfig,
    entity_ids: list[int],
    policy: RemovedEntityPolicy,
) -> int:
    if not entity_ids or policy == RemovedEntityPolicy.keep_all:
        return 0
    q = session.query(Process).filter(Process.entity_id.in_(entity_ids))
    affected = 0
    for proc in q.all():
        if policy == RemovedEntityPolicy.keep_analyzed:
            if proc.status in _ANALYZED_STATUSES:
                continue
            if proc.status in _DOWNLOADED_STATUSES:
                discard_process_downloads(config, proc, session)
                proc.status = ProcessStatus.descartada
                affected += 1
            elif proc.status == ProcessStatus.publicada:
                proc.status = ProcessStatus.descartada
                affected += 1
            continue

        if policy == RemovedEntityPolicy.discard_all:
            if proc.status in _ANALYZED_STATUSES:
                archive_analyzed_process(config, proc)
                affected += 1
            elif proc.status in _DOWNLOADED_STATUSES:
                discard_process_downloads(config, proc, session)
                proc.status = ProcessStatus.descartada
                affected += 1
            elif proc.status == ProcessStatus.publicada:
                proc.status = ProcessStatus.descartada
                affected += 1
    session.flush()
    return affected
