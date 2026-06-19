"""Borrado seguro de carpetas de procesos en disco."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import FeedItem, PipelineItem, ProcessStatus, utcnow
from .list_order import (
    clear_list_ranks,
    enter_analizados_list,
    enter_descargados_list,
    leave_analizados_list,
    leave_descargados_list,
)
from .tenant_paths import procesos_root, remap_process_data_dir, trash_root

logger = logging.getLogger(__name__)

_STATUSES_WITH_DATA = frozenset(
    {
        ProcessStatus.descargando,
        ProcessStatus.descargada,
        ProcessStatus.descartando,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
        ProcessStatus.archivando,
        ProcessStatus.archivada,
    }
)


def _allowed_data_roots(config: AppConfig) -> tuple[Path, ...]:
    return (procesos_root(config), trash_root(config))


def resolve_process_data_dir(config: AppConfig, data_dir: str | None) -> Path | None:
    if not data_dir:
        return None
    data_dir = remap_process_data_dir(config, data_dir) or data_dir
    path = Path(data_dir).resolve()
    for root in _allowed_data_roots(config):
        try:
            path.relative_to(root)
            return path
        except ValueError:
            continue
    logger.warning("data_dir fuera de procesos/trash, no se borra: %s", path)
    return None


def _delete_resolved_path(path: Path | None) -> bool:
    """Borra ruta ya resuelta en disco (sin tocar el modelo FeedItem)."""
    if path is None:
        return False
    if path.is_dir():
        shutil.rmtree(path)
        return True
    if path.exists():
        path.unlink()
        return True
    return False


def _unique_subdir(parent: Path, base_name: str, process_id: int) -> Path:
    """Destino sin colisión; nunca borra un directorio existente."""
    for candidate in (
        parent / base_name,
        parent / f"{process_id}_{base_name}",
    ):
        if not candidate.exists():
            return candidate
    stamp = int(time.time())
    while True:
        candidate = parent / f"{process_id}_{base_name}_{stamp}"
        if not candidate.exists():
            return candidate
        stamp += 1


def delete_process_data_dir(config: AppConfig, process: FeedItem) -> bool:
    """Borra la carpeta del proceso y limpia `data_dir` en el modelo."""
    path = resolve_process_data_dir(config, process.data_dir)
    process.data_dir = None
    if path is None:
        return False
    if path.is_dir():
        shutil.rmtree(path)
        logger.info("Eliminada carpeta proceso id=%s path=%s", process.id, path)
        return True
    if path.exists():
        path.unlink()
        logger.info("Eliminado archivo proceso id=%s path=%s", process.id, path)
        return True
    return False


def clear_process_download_metadata(process: FeedItem) -> None:
    """Quita metadatos de descarga en BD (documentos, ruta)."""
    process.documentos_json = None
    process.data_dir = None


def clear_process_watch_metadata(process: FeedItem) -> None:
    """Quita flags/historial SEACE asociados a una descarga local."""
    process.watch_unread = False
    process.watch_cronograma_prev_json = None
    process.watch_documentos_prev_json = None
    process.watch_changelog_json = None


def delete_process_analysis(session: Session, process: FeedItem) -> None:
    if process.analysis is not None:
        session.delete(process.analysis)
        process.analysis = None


def cleanup_stale_process_data(config: AppConfig, processes: list[FeedItem]) -> int:
    """Quita data_dir en BD/disco para procesos que no deben conservar archivos."""
    removed = 0
    for proc in processes:
        if proc.status in _STATUSES_WITH_DATA:
            continue
        if not proc.data_dir and not proc.documentos_json:
            continue
        delete_process_data_dir(config, proc)
        clear_process_download_metadata(proc)
        removed += 1
    return removed


def cleanup_orphan_dirs(config: AppConfig, root: Path, *, keep_paths: set[Path]) -> int:
    """Borra subcarpetas no referenciadas por procesos activos."""
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.iterdir():
        if not path.is_dir():
            continue
        if path.resolve() in keep_paths:
            continue
        shutil.rmtree(path)
        logger.info("Eliminada carpeta huérfana %s", path)
        removed += 1
    return removed


def cleanup_orphan_process_dirs(config: AppConfig, *, keep_paths: set[Path]) -> int:
    return cleanup_orphan_dirs(config, procesos_root(config), keep_paths=keep_paths)


def cleanup_orphan_trash_dirs(config: AppConfig, *, keep_paths: set[Path]) -> int:
    return cleanup_orphan_dirs(config, trash_root(config), keep_paths=keep_paths)


def process_data_dir_exists(config: AppConfig, process: FeedItem) -> bool:
    if not process.data_dir:
        return False
    path = resolve_process_data_dir(config, process.data_dir)
    return path is not None and path.is_dir()


def resolve_restore_status(config: AppConfig, process: FeedItem) -> ProcessStatus:
    """Estado al restaurar desde descartados según archivos reales en disco."""
    if not process_data_dir_exists(config, process):
        return ProcessStatus.publicada
    if process.analysis and process.analysis.status == "done":
        return ProcessStatus.analizada
    return ProcessStatus.descargada


def discard_process_downloads(
    config: AppConfig, process: FeedItem, session: Session
) -> None:
    """Descarte desde descargada: limpia BD primero, luego borra disco."""
    path = resolve_process_data_dir(config, process.data_dir)
    if path is not None and path.is_dir():
        from .analysis.gemini_session import cleanup_gemini_session

        cleanup_gemini_session(config, path)
    delete_process_analysis(session, process)
    leave_descargados_list(session, process)
    clear_list_ranks(process)
    clear_process_download_metadata(process)
    clear_process_watch_metadata(process)
    session.flush()
    if path is not None and _delete_resolved_path(path):
        logger.info("Eliminada carpeta proceso id=%s path=%s", process.id, path)


def archive_analyzed_process(
    config: AppConfig, process: FeedItem, session: Session
) -> None:
    """Archiva analizado/portafolio: mueve carpeta a trash/, conserva análisis."""
    if process.status == ProcessStatus.archivada:
        return
    leave_analizados_list(session, process)
    src = resolve_process_data_dir(config, process.data_dir)
    trash = trash_root(config)
    trash.mkdir(parents=True, exist_ok=True)

    if src is not None and src.is_dir():
        dest = _unique_subdir(trash, src.name, process.id)
        shutil.move(str(src), str(dest))
        process.data_dir = str(dest.resolve())
    process.status = ProcessStatus.archivada


def restore_archived_process(
    config: AppConfig, process: FeedItem, session: Session
) -> None:
    """Restaura desde archivados: devuelve carpeta a procesos/ y estado analizada."""
    from .db.pipeline_sync import sync_to_pipeline, sync_analysis_to_pipeline
    src = resolve_process_data_dir(config, process.data_dir)
    if src is None or not src.is_dir():
        if process.analysis and process.analysis.status == "done":
            process.status = ProcessStatus.analizada
            enter_analizados_list(session, process)
        else:
            process.status = ProcessStatus.publicada
        sync_to_pipeline(session, process, tenant_id=getattr(session, '_tenant_id', 'default'))
        sync_analysis_to_pipeline(session, process)
        return

    procesos = procesos_root(config)
    procesos.mkdir(parents=True, exist_ok=True)
    dest = _unique_subdir(procesos, src.name, process.id)
    shutil.move(str(src), str(dest))
    process.data_dir = str(dest.resolve())
    if process.analysis and process.analysis.status == "done":
        process.status = ProcessStatus.analizada
        enter_analizados_list(session, process)
    else:
        process.status = ProcessStatus.descargada
        enter_descargados_list(session, process)
    # Sync restored fields (data_dir, status) to PipelineItem (M3 fix)
    sync_to_pipeline(session, process, tenant_id=getattr(session, '_tenant_id', 'default'))
    sync_analysis_to_pipeline(session, process)


def _sync_pipeline_to_feed_batch(session: Session, feed_ids: set[int] | None = None) -> None:
    """Sync critical fields from PipelineItem → FeedItem after maintenance (M4 fix).

    Maintenance functions mutate PipelineItem directly; this helper syncs changes
    back to FeedItem so detail views reading FeedItem stay consistent.
    Skips mock/fake sessions that don't support full ORM queries.
    """
    if not hasattr(session, 'get'):
        return
    q = session.query(PipelineItem)
    if feed_ids is not None:
        q = q.filter(PipelineItem.origin_feed_id.in_(feed_ids))
    for pi in q.all():
        feed = session.get(FeedItem, pi.origin_feed_id)
        if feed is not None:
            feed.status = pi.status
            feed.data_dir = pi.data_dir
            feed.documentos_json = pi.documentos_json
            feed.updated_at = pi.updated_at
            feed.watch_unread = pi.watch_unread
            feed.watch_checked_at = pi.watch_checked_at
            feed.watch_cronograma_prev_json = pi.watch_cronograma_prev_json
            feed.watch_documentos_prev_json = pi.watch_documentos_prev_json
            feed.watch_changelog_json = pi.watch_changelog_json


def recover_stale_workflow_transitions(
    config: AppConfig, session: Session, stale_seconds: int
) -> int:
    """Completa o revierte archivando/descartando colgados."""
    if stale_seconds <= 0:
        return 0
    from datetime import timedelta, timezone

    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    recovered = 0
    transitional = (ProcessStatus.archivando, ProcessStatus.descartando)
    for proc in session.query(PipelineItem).filter(PipelineItem.status.in_(transitional)):
        updated = proc.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated >= cutoff:
            continue
        if proc.status == ProcessStatus.archivando:
            src = resolve_process_data_dir(config, proc.data_dir)
            trash = trash_root(config)
            if src is not None and src.is_dir():
                try:
                    archive_analyzed_process(config, proc, session)
                except Exception:
                    logger.exception(
                        "Archivo obsoleto falló id=%s; revierte a analizada",
                        proc.id,
                    )
                    proc.status = ProcessStatus.analizada
            elif src is not None and trash in src.parents:
                proc.status = ProcessStatus.archivada
            else:
                proc.status = ProcessStatus.analizada
        else:
            path = resolve_process_data_dir(config, proc.data_dir)
            try:
                if path is not None and path.is_dir():
                    discard_process_downloads(config, proc, session)
                proc.status = ProcessStatus.descartada
            except Exception:
                logger.exception(
                    "Descarte obsoleto falló id=%s; revierte a descargada",
                    proc.id,
                )
                proc.status = ProcessStatus.descargada
        recovered += 1
        logger.warning(
            "Transición obsoleta recuperada: proceso id=%s status=%s",
            proc.id,
            proc.status.value,
        )
    # Sync maintenance changes back to FeedItem (M4 fix)
    if recovered > 0:
        _sync_pipeline_to_feed_batch(session)
    return recovered


def recover_stale_downloads(
    config: AppConfig, session: Session, stale_seconds: int
) -> int:
    """Resetea descargas colgadas en descargando con carpeta parcial o sin progreso."""
    if stale_seconds <= 0:
        return 0
    from datetime import timedelta, timezone

    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    recovered = 0
    for proc in session.query(PipelineItem).filter(PipelineItem.status == ProcessStatus.descargando):
        updated = proc.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        if updated >= cutoff:
            continue
        delete_process_data_dir(config, proc)
        clear_process_download_metadata(proc)
        proc.status = ProcessStatus.publicada
        recovered += 1
        logger.warning(
            "Descarga obsoleta recuperada: proceso id=%s nid=%s",
            proc.id,
            proc.nid_proceso,
        )
    # Sync maintenance changes back to FeedItem (M4 fix)
    if recovered > 0:
        _sync_pipeline_to_feed_batch(session)
    return recovered


def repair_processes_missing_data(config: AppConfig, session: Session) -> int:
    """Procesos descargados/analizados sin carpeta en disco → publicada."""
    needs_data = {
        ProcessStatus.descargando,
        ProcessStatus.descargada,
        ProcessStatus.descartando,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
        ProcessStatus.archivando,
    }
    repaired = 0
    for proc in session.query(PipelineItem).filter(PipelineItem.status.in_(needs_data)):
        if process_data_dir_exists(config, proc):
            continue
        clear_process_download_metadata(proc)
        clear_list_ranks(proc)
        delete_process_analysis(session, proc)
        proc.status = ProcessStatus.publicada
        repaired += 1
    # Sync maintenance changes back to FeedItem (M4 fix)
    if repaired > 0:
        _sync_pipeline_to_feed_batch(session)
    return repaired


def repair_archived_processes(config: AppConfig, session: Session) -> int:
    """Archivados sin carpeta en trash → analizada (si hay análisis) o publicada."""
    repaired = 0
    for proc in session.query(PipelineItem).filter(PipelineItem.status == ProcessStatus.archivada):
        if process_data_dir_exists(config, proc):
            continue
        clear_process_download_metadata(proc)
        if proc.analysis and proc.analysis.status == "done":
            proc.status = ProcessStatus.analizada
            enter_analizados_list(session, proc)
        else:
            delete_process_analysis(session, proc)
            proc.status = ProcessStatus.publicada
        repaired += 1
    # Sync maintenance changes back to FeedItem (M4 fix)
    if repaired > 0:
        _sync_pipeline_to_feed_batch(session)
    return repaired


def repair_discarded_processes(config: AppConfig, session: Session) -> int:
    """Descartados con restos de descarga/análisis en BD o disco."""
    repaired = 0
    for proc in session.query(PipelineItem).filter(PipelineItem.status == ProcessStatus.descartada):
        if (
            proc.data_dir is None
            and proc.documentos_json is None
            and proc.analysis is None
        ):
            continue
        delete_process_data_dir(config, proc)
        clear_process_download_metadata(proc)
        delete_process_analysis(session, proc)
        repaired += 1
    # Sync maintenance changes back to FeedItem (M4 fix)
    if repaired > 0:
        _sync_pipeline_to_feed_batch(session)
    return repaired


def purge_all_stale_process_data(config: AppConfig, session: Session) -> tuple[int, int]:
    """Retroactivo: procesos descartados/publicados con data_dir + dirs huérfanas."""
    processes = session.query(PipelineItem).all()
    db_cleaned = cleanup_stale_process_data(config, processes)
    # Sync maintenance changes back to FeedItem (M4 fix)
    _sync_pipeline_to_feed_batch(session)

    keep_paths: set[Path] = set()
    for proc in processes:
        if not proc.data_dir or proc.status not in _STATUSES_WITH_DATA:
            continue
        path = resolve_process_data_dir(config, proc.data_dir)
        if path is not None and path.is_dir():
            keep_paths.add(path)

    orphans = cleanup_orphan_process_dirs(config, keep_paths=keep_paths)
    trash_orphans = cleanup_orphan_trash_dirs(config, keep_paths=keep_paths)
    return db_cleaned, orphans + trash_orphans
