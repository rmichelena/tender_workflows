"""Rutas de configuración — entidades a escanear."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.models import Entity
from ..entity_catalog import ensure_entity_catalog
from ..entity_process_cleanup import (
    apply_removed_entity_policy,
    count_processes_for_entities,
)
from ..scan_options import (
    RemovedEntityPolicy,
    ScanDateMode,
    ScanOptions,
    default_since_date,
    parse_ddmmyy,
)
from ..scanner import MultiEntityScanner

logger = logging.getLogger(__name__)
LIMA = ZoneInfo("America/Lima")


class EntitiesPreviewRequest(BaseModel):
    selected_rucs: list[str] = Field(default_factory=list)


class EntitiesSaveRequest(BaseModel):
    selected_rucs: list[str] = Field(default_factory=list)
    added_scan_mode: str | None = None
    since_date: str | None = None
    removed_policy: str = RemovedEntityPolicy.keep_all.value


def _entity_payload(ent: Entity) -> dict[str, Any]:
    return {
        "id": ent.id,
        "ruc": ent.ruc,
        "nombre": ent.nombre,
        "activa": bool(ent.activa),
        "estado_osce": ent.estado_osce or "",
        "departamento": ent.departamento or "",
        "provincia": ent.provincia or "",
        "distrito": ent.distrito or "",
    }


def _normalize_rucs(rucs: list[str]) -> set[str]:
    return {"".join(c for c in r if c.isdigit()) for r in rucs if r}


def _diff_selection(
    session: Session, selected_rucs: set[str]
) -> tuple[set[str], list[int], list[Entity]]:
    entities = session.query(Entity).order_by(Entity.nombre).all()
    current_active = {e.ruc for e in entities if e.activa}
    added = selected_rucs - current_active
    removed_rucs = current_active - selected_rucs
    removed_ids = [e.id for e in entities if e.ruc in removed_rucs]
    by_ruc = {e.ruc: e for e in entities}
    added_entities = [by_ruc[r] for r in sorted(added) if r in by_ruc]
    return added, removed_ids, added_entities


def _validate_save_request(
    body: EntitiesSaveRequest,
    *,
    entities: list[Entity],
    selected: set[str],
    added_entities: list[Entity],
) -> tuple[RemovedEntityPolicy, ScanOptions | None]:
    known = {e.ruc for e in entities}
    unknown = selected - known
    if unknown:
        raise HTTPException(400, f"RUC desconocidos: {', '.join(sorted(unknown)[:5])}")

    try:
        policy = RemovedEntityPolicy(body.removed_policy)
    except ValueError as exc:
        raise HTTPException(400, "Política de eliminación inválida") from exc

    scan_options: ScanOptions | None = None
    if added_entities and body.added_scan_mode:
        try:
            mode = ScanDateMode(body.added_scan_mode)
        except ValueError as exc:
            raise HTTPException(400, "Modo de escaneo inválido") from exc
        since: date | None = None
        if mode == ScanDateMode.since_date:
            if not body.since_date:
                raise HTTPException(400, "Fecha requerida")
            since = parse_ddmmyy(body.since_date)
            if since is None:
                raise HTTPException(
                    400,
                    f"Fecha inválida o inexistente: {body.since_date!r} "
                    "(use dd/mm/yy, día real del calendario)",
                )
        scan_options = ScanOptions(
            entity_ids=frozenset(e.id for e in added_entities),
            date_mode=mode,
            since_date=since,
            multipage=True,
        )

    return policy, scan_options


def register_settings_routes(app, config: AppConfig, render, get_db):
    @app.get("/settings/entidades", response_class=HTMLResponse)
    def settings_entidades(request: Request, db: Session = Depends(get_db)):
        now = datetime.now(LIMA)
        return render(
            request,
            "settings_entidades.html",
            db=db,
            active_page="settings_entidades",
            current_year=now.year,
            default_since_date=default_since_date(now).strftime("%d/%m/%y"),
        )

    @app.get("/api/settings/entidades")
    def api_list_entities(db: Session = Depends(get_db)):
        rows = db.query(Entity).order_by(Entity.nombre).all()
        return {
            "entities": [_entity_payload(e) for e in rows],
            "selected_count": sum(1 for e in rows if e.activa),
            "total_count": len(rows),
        }

    @app.post("/api/settings/entidades/preview")
    def api_preview_entities(
        body: EntitiesPreviewRequest, db: Session = Depends(get_db)
    ):
        selected = _normalize_rucs(body.selected_rucs)
        added, removed_ids, added_entities = _diff_selection(db, selected)
        counts = count_processes_for_entities(db, list(removed_ids))
        return {
            "added": [{"ruc": e.ruc, "nombre": e.nombre} for e in added_entities],
            "removed_entity_ids": list(removed_ids),
            "removed_counts": {
                "publicados": counts.publicados,
                "descargados": counts.descargados,
                "analizados": counts.analizados,
            },
            "current_year": datetime.now(LIMA).year,
            "default_since_date": default_since_date().strftime("%d/%m/%y"),
        }

    @app.post("/api/settings/entidades/save")
    def api_save_entities(
        body: EntitiesSaveRequest,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
    ):
        selected = _normalize_rucs(body.selected_rucs)
        entities = db.query(Entity).all()
        added_rucs, removed_ids, added_entities = _diff_selection(db, selected)
        policy, scan_options = _validate_save_request(
            body,
            entities=entities,
            selected=selected,
            added_entities=added_entities,
        )

        for ent in entities:
            ent.activa = ent.ruc in selected

        cleanup = apply_removed_entity_policy(db, config, list(removed_ids), policy)
        db.commit()

        if scan_options:
            entity_ids = list(scan_options.entity_ids or [])
            background_tasks.add_task(_run_targeted_scan, config, entity_ids, scan_options)

        return JSONResponse(
            {
                "ok": True,
                "added_scan_scheduled": scan_options is not None,
                "added_count": len(added_rucs),
                "removed_count": len(removed_ids),
                "cleanup_affected": cleanup.affected,
                "cleanup_deferred": cleanup.deferred,
            }
        )


def _run_targeted_scan(
    config: AppConfig, entity_ids: list[int], options: ScanOptions
) -> None:
    from ..db.session import init_db, session_factory

    init_db(config.database_url)
    session = session_factory()
    try:
        scanner = MultiEntityScanner(config, session)
        n = scanner.run_once(options)
        session.commit()
        logger.info(
            "Escaneo acotado completado: %s entidad(es), %s proceso(s) nuevo(s)",
            len(entity_ids),
            n,
        )
    except Exception:
        session.rollback()
        logger.exception("Escaneo acotado falló")
    finally:
        session.close()


def bootstrap_entities(session: Session, config: AppConfig) -> None:
    ensure_entity_catalog(session, config)
