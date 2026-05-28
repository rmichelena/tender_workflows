"""Consultas reutilizables sobre Process."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..db.models import Process


def get_process_or_404(
    db: Session,
    process_id: int,
    *,
    with_entity: bool = False,
    with_analysis: bool = False,
) -> Process:
    opts = []
    if with_entity:
        opts.append(joinedload(Process.entity))
    if with_analysis:
        opts.append(joinedload(Process.analysis))
    if opts:
        proc = (
            db.query(Process)
            .options(*opts)
            .filter(Process.id == process_id)
            .one_or_none()
        )
    else:
        proc = db.get(Process, process_id)
    if proc is None:
        raise HTTPException(404)
    return proc
