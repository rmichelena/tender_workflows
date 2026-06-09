"""Consultas reutilizables sobre FeedItem."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.exc import NoResultFound

from ..db.models import FeedItem


def get_process_or_404(
    db: Session,
    process_id: int,
    *,
    with_entity: bool = False,
    with_analysis: bool = False,
) -> FeedItem:
    opts = []
    if with_entity:
        opts.append(joinedload(FeedItem.entity))
    if with_analysis:
        opts.append(joinedload(FeedItem.analysis))
    if opts:
        try:
            proc = (
                db.query(FeedItem)
                .options(*opts)
                .filter(FeedItem.id == process_id)
                .one()
            )
        except NoResultFound:
            raise HTTPException(404) from None
    else:
        proc = db.get(FeedItem, process_id)
        if proc is None:
            raise HTTPException(404)
    return proc
