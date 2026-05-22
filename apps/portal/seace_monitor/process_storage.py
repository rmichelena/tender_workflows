"""Borrado seguro de carpetas de procesos en disco."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Process, ProcessStatus
from .tenant_paths import procesos_root, remap_process_data_dir

logger = logging.getLogger(__name__)

_STATUSES_WITH_DATA = frozenset(
    {
        ProcessStatus.descargando,
        ProcessStatus.descargada,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
    }
)


def resolve_process_data_dir(config: AppConfig, data_dir: str | None) -> Path | None:
    if not data_dir:
        return None
    data_dir = remap_process_data_dir(config, data_dir) or data_dir
    path = Path(data_dir).resolve()
    root = procesos_root(config)
    try:
        path.relative_to(root)
    except ValueError:
        logger.warning("data_dir fuera de procesos/, no se borra: %s", path)
        return None
    return path


def delete_process_data_dir(config: AppConfig, process: Process) -> bool:
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


def clear_process_download_metadata(process: Process) -> None:
    """Quita metadatos de descarga en BD (documentos, ruta)."""
    process.documentos_json = None
    process.data_dir = None


def delete_process_analysis(session: Session, process: Process) -> None:
    if process.analysis is not None:
        session.delete(process.analysis)
        process.analysis = None


def cleanup_stale_process_data(config: AppConfig, processes: list[Process]) -> int:
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


def cleanup_orphan_process_dirs(config: AppConfig, *, keep_paths: set[Path]) -> int:
    """Borra subcarpetas en data/procesos no referenciadas por procesos activos."""
    root = procesos_root(config)
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


def process_data_dir_exists(config: AppConfig, process: Process) -> bool:
    if not process.data_dir:
        return False
    path = resolve_process_data_dir(config, process.data_dir)
    return path is not None and path.is_dir()


def resolve_restore_status(config: AppConfig, process: Process) -> ProcessStatus:
    """Estado al restaurar desde descartados según archivos reales en disco."""
    if not process_data_dir_exists(config, process):
        return ProcessStatus.publicada
    if process.analysis and process.analysis.status == "done":
        return ProcessStatus.analizada
    return ProcessStatus.descargada


def discard_process_downloads(
    config: AppConfig, process: Process, session: Session
) -> None:
    """Descarte con datos locales: disco, metadatos de descarga y análisis."""
    delete_process_data_dir(config, process)
    clear_process_download_metadata(process)
    delete_process_analysis(session, process)


def repair_processes_missing_data(config: AppConfig, session: Session) -> int:
    """Procesos descargados/analizados sin carpeta en disco → publicada."""
    needs_data = {
        ProcessStatus.descargando,
        ProcessStatus.descargada,
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
    }
    repaired = 0
    for proc in session.query(Process).filter(Process.status.in_(needs_data)):
        if process_data_dir_exists(config, proc):
            continue
        clear_process_download_metadata(proc)
        delete_process_analysis(session, proc)
        proc.status = ProcessStatus.publicada
        repaired += 1
    return repaired


def repair_discarded_processes(config: AppConfig, session: Session) -> int:
    """Descartados con restos de descarga/análisis en BD o disco."""
    repaired = 0
    for proc in session.query(Process).filter(Process.status == ProcessStatus.descartada):
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
    return repaired


def purge_all_stale_process_data(config: AppConfig, session: Session) -> tuple[int, int]:
    """Retroactivo: procesos descartados/publicados con data_dir + dirs huérfanas."""
    processes = session.query(Process).all()
    db_cleaned = cleanup_stale_process_data(config, processes)

    keep_paths: set[Path] = set()
    for proc in processes:
        if not proc.data_dir or proc.status not in _STATUSES_WITH_DATA:
            continue
        path = resolve_process_data_dir(config, proc.data_dir)
        if path is not None and path.is_dir():
            keep_paths.add(path)

    orphans = cleanup_orphan_process_dirs(config, keep_paths=keep_paths)
    return db_cleaned, orphans
