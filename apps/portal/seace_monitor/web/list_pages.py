"""Renderizado compartido de listas de workflow (descargados, analizados, archivados).

Las listas de pipeline leen de `PipelineItem` (0.3e-3); las de feed siguen leyendo
`FeedItem` vía `FeedRepository`.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

from ..db.list_views import build_pipeline_list_views
from ..db.models import PipelineItem, ProcessStatus
from ..feed import FeedRepository
from ..feed.pipeline_repository import PipelineRepository
from .sorting import (
    WORKFLOW_LIST_DEFAULT_SORT,
    WORKFLOW_LIST_SORT_COLUMNS,
    build_sort_query,
    normalize_dir,
    normalize_sort,
    sort_process_list_views,
)


def render_workflow_list(
    request: Request,
    db: Session,
    render,
    *,
    template: str,
    active_page: str,
    statuses: list[ProcessStatus],
    rank_attr: str,
    sort: str | None,
    dir: str | None,
    extra_context: dict | None = None,
) -> HTMLResponse:
    sort_col = normalize_sort(sort, default=WORKFLOW_LIST_DEFAULT_SORT)
    sort_dir = normalize_dir(dir, sort_col)
    # Pipeline reads from pipeline_items (0.3e-3)
    rows = (
        PipelineRepository(db)
        .query_by_status(statuses)
        .options(joinedload(PipelineItem.entity), joinedload(PipelineItem.analysis))
        .all()
    )
    processes = sort_process_list_views(
        build_pipeline_list_views(rows, rank_attr=rank_attr),
        sort_col,
        sort_dir,
        default_sort=WORKFLOW_LIST_DEFAULT_SORT,
    )

    def sort_href(column: str) -> str:
        return build_sort_query(column, sort=sort_col, direction=sort_dir)

    ctx = {
        "processes": processes,
        "sort": sort_col,
        "sort_dir": sort_dir,
        "sort_columns": WORKFLOW_LIST_SORT_COLUMNS,
        "sort_href": sort_href,
        **(extra_context or {}),
    }
    return render(request, template, db=db, active_page=active_page, **ctx)
