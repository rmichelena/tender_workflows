"""Background jobs para transiciones de estado con rollback consistente."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from ..analysis.runner import AnalysisRunner
from ..db.models import Process, ProcessStatus, utcnow
from ..db.session import commit_session_with_retry, session_factory
from ..feed import promote
from ..process_storage import (
    archive_analyzed_process,
    clear_process_download_metadata,
    delete_process_data_dir,
    discard_process_downloads,
)

if TYPE_CHECKING:
    from ..config import AppConfig

logger = logging.getLogger(__name__)


def run_status_transition_job(
    config: AppConfig,
    process_id: int,
    *,
    expected_status: ProcessStatus,
    work: Callable[[Session, Process], None],
    rollback_status: ProcessStatus | Callable[[Process], ProcessStatus],
    log_label: str,
    on_rollback: Callable[[Session, Process], None] | None = None,
) -> None:
    """Ejecuta work mientras el proceso sigue en expected_status; revierte en error."""
    session = session_factory()
    try:
        proc = session.get(Process, process_id)
        if proc is None or proc.status != expected_status:
            return
        work(session, proc)
        session.commit()
    except Exception:
        session.rollback()
        proc = session.get(Process, process_id)
        if proc is not None and proc.status == expected_status:
            if callable(rollback_status):
                proc.status = rollback_status(proc)
            else:
                proc.status = rollback_status
            if on_rollback:
                on_rollback(session, proc)
            session.commit()
        logger.exception("%s failed for process %s", log_label, process_id)
    finally:
        session.close()


def schedule_status_transition(
    background_tasks: BackgroundTasks,
    config: AppConfig,
    process_id: int,
    *,
    expected_status: ProcessStatus,
    work: Callable[[Session, Process], None],
    rollback_status: ProcessStatus | Callable[[Process], ProcessStatus],
    log_label: str,
    on_rollback: Callable[[Session, Process], None] | None = None,
) -> None:
    background_tasks.add_task(
        run_status_transition_job,
        config,
        process_id,
        expected_status=expected_status,
        work=work,
        rollback_status=rollback_status,
        log_label=log_label,
        on_rollback=on_rollback,
    )


def run_download_job(config: AppConfig, process_id: int) -> None:
    session = session_factory()
    try:
        runner = AnalysisRunner(config, session)
        runner.download(process_id)
    except Exception:
        proc = session.get(Process, process_id)
        if proc is not None:
            proc.status = ProcessStatus.publicada
            delete_process_data_dir(config, proc)
            clear_process_download_metadata(proc)
            session.commit()
        logger.exception("Background download failed")
    finally:
        session.close()


def schedule_download(
    background_tasks: BackgroundTasks,
    config: AppConfig,
    process_id: int,
) -> None:
    background_tasks.add_task(run_download_job, config, process_id)


def begin_download_transition(db: Session, proc: Process) -> int:
    proc.status = ProcessStatus.descargando
    # Acción positiva → promoción feed→pipeline (0.3d): el item deja de ser feed puro.
    promote(db, proc)
    commit_session_with_retry(db)
    process_id = proc.id
    db.expunge(proc)
    return process_id


def begin_discard_transition(db: Session, proc: Process) -> tuple[int, ProcessStatus]:
    proc.status = ProcessStatus.descartando
    proc.updated_at = utcnow()
    process_id = proc.id
    db.commit()
    return process_id, ProcessStatus.descartada


def begin_archive_transition(
    db: Session, proc: Process
) -> tuple[int, ProcessStatus]:
    restore_status = proc.status
    proc.status = ProcessStatus.archivando
    proc.updated_at = utcnow()
    process_id = proc.id
    db.commit()
    return process_id, restore_status


def discard_work(config: AppConfig, session: Session, proc: Process) -> None:
    discard_process_downloads(config, proc, session)
    proc.status = ProcessStatus.descartada


def archive_work(config: AppConfig, session: Session, proc: Process) -> None:
    archive_analyzed_process(config, proc, session)
