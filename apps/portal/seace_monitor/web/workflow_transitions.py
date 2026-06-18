"""Background jobs para transiciones de estado con rollback consistente."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from ..analysis.runner import AnalysisRunner
from ..db.models import FeedItem, PipelineItem, ProcessStatus, utcnow
from ..db.session import commit_session_with_retry, session_factory
from ..feed.pipeline_repository import get_pipeline_item_by_feed_id
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


def _sync_status(feed: FeedItem, pi: PipelineItem | None) -> None:
    """Explicit status sync: FeedItem ← PipelineItem (replaces implicit dual-write)."""
    if pi is not None:
        feed.status = pi.status
        feed.updated_at = pi.updated_at


def run_status_transition_job(
    config: AppConfig,
    process_id: int,
    *,
    expected_status: ProcessStatus,
    work: Callable[[Session, PipelineItem], None],
    rollback_status: ProcessStatus | Callable[[PipelineItem], ProcessStatus],
    log_label: str,
    on_rollback: Callable[[Session, PipelineItem], None] | None = None,
) -> None:
    """Ejecuta work mientras el proceso sigue en expected_status; revierte en error."""
    session = session_factory()
    try:
        pi = get_pipeline_item_by_feed_id(session, process_id)
        if pi is None or pi.status != expected_status:
            return
        work(session, pi)
        session.commit()
    except Exception:
        session.rollback()
        pi = get_pipeline_item_by_feed_id(session, process_id)
        if pi is not None and pi.status == expected_status:
            if callable(rollback_status):
                pi.status = rollback_status(pi)
            else:
                pi.status = rollback_status
            if on_rollback:
                on_rollback(session, pi)
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
    work: Callable[[Session, PipelineItem], None],
    rollback_status: ProcessStatus | Callable[[PipelineItem], ProcessStatus],
    log_label: str,
    on_rollback: Callable[[Session, PipelineItem], None] | None = None,
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
        pi = get_pipeline_item_by_feed_id(session, process_id)
        if pi is not None:
            pi.status = ProcessStatus.publicada
            delete_process_data_dir(config, pi)
            clear_process_download_metadata(pi)
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


def begin_download_transition(db: Session, proc: FeedItem) -> int:
    # Acción positiva → promoción feed→pipeline
    promote(db, proc)
    pi = get_pipeline_item_by_feed_id(db, proc.id)
    if pi is None:
        # Fallback: sync via pipeline_sync if PipelineItem not yet created
        from ..db.pipeline_sync import sync_to_pipeline
        pi = sync_to_pipeline(db, proc)
    pi.status = ProcessStatus.descargando
    proc.status = ProcessStatus.descargando  # keep FeedItem in sync for detail views
    commit_session_with_retry(db)
    process_id = proc.id
    db.expunge(proc)
    return process_id


def begin_discard_transition(db: Session, proc: FeedItem) -> tuple[int, ProcessStatus]:
    pi = get_pipeline_item_by_feed_id(db, proc.id)
    if pi is not None:
        pi.status = ProcessStatus.descartando
        pi.updated_at = utcnow()
    proc.status = ProcessStatus.descartando
    proc.updated_at = utcnow()
    process_id = proc.id
    db.commit()
    return process_id, ProcessStatus.descartada


def begin_archive_transition(
    db: Session, proc: FeedItem
) -> tuple[int, ProcessStatus]:
    pi = get_pipeline_item_by_feed_id(db, proc.id)
    restore_status = proc.status
    if pi is not None:
        pi.status = ProcessStatus.archivando
        pi.updated_at = utcnow()
    proc.status = ProcessStatus.archivando
    proc.updated_at = utcnow()
    process_id = proc.id
    db.commit()
    return process_id, restore_status


def discard_work(config: AppConfig, session: Session, pi: PipelineItem) -> None:
    discard_process_downloads(config, pi, session)
    pi.status = ProcessStatus.descartada


def archive_work(config: AppConfig, session: Session, pi: PipelineItem) -> None:
    archive_analyzed_process(config, pi, session)
