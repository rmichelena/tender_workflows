"""Tareas de recuperación y mantenimiento de BD."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .models import AnalysisResult, Process, ProcessStatus, utcnow


def recover_stale_analyses(
    session: Session, stale_seconds: int, *, config=None
) -> int:
    """Marca análisis `running` antiguos como error (crash/restart)."""
    if stale_seconds <= 0:
        return 0
    from pathlib import Path

    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    stale = (
        session.query(AnalysisResult)
        .filter(
            AnalysisResult.status == "running",
            AnalysisResult.started_at.isnot(None),
            AnalysisResult.started_at < cutoff,
        )
        .all()
    )
    for analysis in stale:
        analysis.status = "error"
        analysis.error_message = (
            "Análisis interrumpido (servidor reiniciado o timeout). Reintenta."
        )
        analysis.finished_at = utcnow()
        if config is not None:
            proc = session.get(Process, analysis.process_id)
            if proc is not None and proc.data_dir:
                from ..analysis.gemini_session import (
                    clean_run_scoped_artifacts,
                    cleanup_gemini_session,
                )

                proc_dir = Path(proc.data_dir)
                cleanup_gemini_session(config, proc_dir)
                clean_run_scoped_artifacts(proc_dir)
    if stale:
        session.commit()
    return len(stale)


def abandon_stale_analysis_run(
    session: Session, process_id: int, run_id: str | None, *, message: str
) -> bool:
    """Marca error solo si el run_id sigue siendo el activo."""
    analysis = (
        session.query(AnalysisResult)
        .filter(AnalysisResult.process_id == process_id)
        .one_or_none()
    )
    if analysis is None:
        return False
    if run_id and analysis.run_id != run_id:
        return False
    if analysis.status != "running":
        return False
    analysis.status = "error"
    analysis.error_message = message
    analysis.finished_at = utcnow()
    proc = session.get(Process, process_id)
    if proc is not None and proc.status not in (
        ProcessStatus.descargada,
        ProcessStatus.portafolio,
    ):
        proc.status = ProcessStatus.descargada
    session.commit()
    return True


def is_stale_running_analysis(analysis: AnalysisResult, stale_seconds: int) -> bool:
    if analysis.status != "running" or not analysis.started_at:
        return False
    if stale_seconds <= 0:
        return False
    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    started = analysis.started_at
    if started.tzinfo is None:
        from datetime import timezone

        started = started.replace(tzinfo=timezone.utc)
    return started < cutoff
