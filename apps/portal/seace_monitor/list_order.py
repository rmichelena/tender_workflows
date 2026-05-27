"""Correlativo de orden en listas descargados / analizados."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db.models import Process, ProcessStatus

DESCARGADOS_LIST_STATUSES = frozenset(
    {ProcessStatus.descargada, ProcessStatus.descartando}
)
ANALIZADOS_LIST_STATUSES = frozenset(
    {
        ProcessStatus.analizada,
        ProcessStatus.portafolio,
        ProcessStatus.archivando,
    }
)


def _append_rank(session: Session, proc: Process, attr: str, statuses: frozenset) -> None:
    session.flush()
    current_max = (
        session.query(func.max(getattr(Process, attr)))
        .filter(
            Process.status.in_(tuple(statuses)),
            Process.id != proc.id,
        )
        .scalar()
    )
    setattr(proc, attr, int(current_max or 0) + 1)


def _renumber_list(
    session: Session, statuses: frozenset[ProcessStatus], attr: str
) -> None:
    rows = (
        session.query(Process)
        .filter(
            Process.status.in_(tuple(statuses)),
            getattr(Process, attr).isnot(None),
        )
        .order_by(getattr(Process, attr).asc(), Process.id.asc())
        .all()
    )
    for index, row in enumerate(rows, start=1):
        setattr(row, attr, index)
    session.flush()


def enter_descargados_list(session: Session, proc: Process) -> None:
    if proc.status not in DESCARGADOS_LIST_STATUSES:
        return
    _append_rank(session, proc, "list_rank_descargados", DESCARGADOS_LIST_STATUSES)


def leave_descargados_list(session: Session, proc: Process) -> None:
    if proc.list_rank_descargados is None:
        return
    proc.list_rank_descargados = None
    session.flush()
    _renumber_list(session, DESCARGADOS_LIST_STATUSES, "list_rank_descargados")


def enter_analizados_list(session: Session, proc: Process) -> None:
    if proc.status not in ANALIZADOS_LIST_STATUSES:
        return
    _append_rank(session, proc, "list_rank_analizados", ANALIZADOS_LIST_STATUSES)


def leave_analizados_list(session: Session, proc: Process) -> None:
    if proc.list_rank_analizados is None:
        return
    proc.list_rank_analizados = None
    session.flush()
    _renumber_list(session, ANALIZADOS_LIST_STATUSES, "list_rank_analizados")


def clear_list_ranks(proc: Process) -> None:
    proc.list_rank_descargados = None
    proc.list_rank_analizados = None


def backfill_list_ranks(session: Session) -> int:
    """Asigna correlativos contiguos 1..n en cada lista (legacy sin rank)."""
    updated = 0
    for statuses, attr in (
        (DESCARGADOS_LIST_STATUSES, "list_rank_descargados"),
        (ANALIZADOS_LIST_STATUSES, "list_rank_analizados"),
    ):
        missing = (
            session.query(Process.id)
            .filter(
                Process.status.in_(tuple(statuses)),
                getattr(Process, attr).is_(None),
            )
            .first()
        )
        if missing is None:
            continue
        rows = (
            session.query(Process)
            .filter(Process.status.in_(tuple(statuses)))
            .order_by(
                getattr(Process, attr).asc().nulls_last(),
                Process.updated_at.asc(),
                Process.id.asc(),
            )
            .all()
        )
        for index, row in enumerate(rows, start=1):
            if getattr(row, attr) != index:
                setattr(row, attr, index)
                updated += 1
    return updated
