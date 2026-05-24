"""Políticas al quitar entidades del escaneo."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .config import AppConfig
from .db.maintenance import is_stale_running_analysis
from .db.models import Process, ProcessStatus
from .process_storage import archive_analyzed_process, discard_process_downloads
from .scan_options import RemovedEntityPolicy

_ANALYZED_STATUSES = frozenset(
    {ProcessStatus.analizada, ProcessStatus.portafolio}
)
_DOWNLOADED_STATUSES = frozenset({ProcessStatus.descargada})
_PUBLIC_STATUSES = frozenset({ProcessStatus.publicada})


@dataclass(frozen=True)
class EntityProcessCounts:
    publicados: int
    descargados: int
    analizados: int

    @property
    def total(self) -> int:
        return self.publicados + self.descargados + self.analizados


@dataclass(frozen=True)
class EntityCleanupResult:
    affected: int
    deferred: int


def _process_is_busy(proc: Process, stale_seconds: int) -> bool:
    if proc.status == ProcessStatus.descargando:
        return True
    if proc.analysis and proc.analysis.status == "running":
        return not is_stale_running_analysis(proc.analysis, stale_seconds)
    return False


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
        elif status in _DOWNLOADED_STATUSES or status == ProcessStatus.descargando:
            descargados += 1
        elif status in _ANALYZED_STATUSES or status == ProcessStatus.archivada:
            analizados += 1
    return EntityProcessCounts(publicados, descargados, analizados)


def apply_removed_entity_policy(
    session: Session,
    config: AppConfig,
    entity_ids: list[int],
    policy: RemovedEntityPolicy,
) -> EntityCleanupResult:
    if not entity_ids or policy == RemovedEntityPolicy.keep_all:
        return EntityCleanupResult(0, 0)
    stale_seconds = config.stale_analysis_seconds
    q = session.query(Process).filter(Process.entity_id.in_(entity_ids))
    affected = deferred = 0
    for proc in q.all():
        if _process_is_busy(proc, stale_seconds):
            deferred += 1
            continue

        if policy == RemovedEntityPolicy.keep_analyzed:
            if proc.status in _ANALYZED_STATUSES or proc.status == ProcessStatus.archivada:
                continue
            if proc.status in _DOWNLOADED_STATUSES:
                proc.status = ProcessStatus.descartada
                discard_process_downloads(config, proc, session)
                affected += 1
            elif proc.status == ProcessStatus.publicada:
                proc.status = ProcessStatus.descartada
                affected += 1
            continue

        if policy == RemovedEntityPolicy.discard_all:
            if proc.status == ProcessStatus.archivada:
                continue
            if proc.status in _ANALYZED_STATUSES:
                archive_analyzed_process(config, proc)
                affected += 1
            elif proc.status in _DOWNLOADED_STATUSES:
                proc.status = ProcessStatus.descartada
                discard_process_downloads(config, proc, session)
                affected += 1
            elif proc.status == ProcessStatus.publicada:
                proc.status = ProcessStatus.descartada
                affected += 1
    session.flush()
    return EntityCleanupResult(affected, deferred)
