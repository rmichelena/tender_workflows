"""Tareas de recuperación y mantenimiento de BD."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .models import AnalysisResult, Process, ProcessStatus, utcnow


def recover_stale_analyses(session: Session, stale_seconds: int) -> int:
    """Marca análisis `running` antiguos como error (crash/restart)."""
    if stale_seconds <= 0:
        return 0
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
    if stale:
        session.commit()
    return len(stale)


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
