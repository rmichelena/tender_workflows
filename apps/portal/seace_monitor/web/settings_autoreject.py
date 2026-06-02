"""Rutas de configuración de reglas autoreject."""

from __future__ import annotations

import logging

import yaml
from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from ..auto_reject import (
    active_auto_reject_rules_path,
    apply_auto_reject_rules,
    editable_auto_reject_rules_path,
    load_auto_reject_rules,
    validate_rules_yaml,
)
from ..config import AppConfig
from ..db.models import Process, ProcessStatus
from ..feed import record_autoreject_decision
from ..ingest import get_adapter, registered_sources

logger = logging.getLogger(__name__)


def _rules_text(config: AppConfig) -> tuple[str, bool]:
    active = active_auto_reject_rules_path(config)
    return active.read_text(encoding="utf-8"), active == editable_auto_reject_rules_path(config)


def _scan_channels() -> list[dict[str, str]]:
    """Canales (inputs) con listado escaneable, para aplicar reglas a lo existente."""
    channels: list[dict[str, str]] = []
    for source in registered_sources():
        adapter = get_adapter(source)
        if adapter.capabilities.scan_listings:
            channels.append({"source": source, "label": adapter.label})
    return channels


def register_autoreject_settings_routes(app, config: AppConfig, render, _get_db):
    @app.get("/settings/autoreject", response_class=HTMLResponse)
    def settings_autoreject(
        request: Request,
        saved: str | None = None,
        applied: int | None = None,
    ):
        text, using_override = _rules_text(config)
        return render(
            request,
            "settings_autoreject.html",
            active_page="settings_autoreject",
            rules_yaml=text,
            using_override=using_override,
            saved=bool(saved),
            applied=applied,
            channels=_scan_channels(),
            editable_path=editable_auto_reject_rules_path(config),
        )

    @app.post("/settings/autoreject", response_class=HTMLResponse)
    def save_settings_autoreject(
        request: Request,
        rules_yaml: str = Form(...),
        action: str = Form("save"),
    ):
        if action != "save":
            return RedirectResponse("/settings/autoreject", status_code=303)
        try:
            validated_rules = validate_rules_yaml(rules_yaml)
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(400, f"YAML inválido: {exc}") from exc
        if not validated_rules:
            raise HTTPException(
                400,
                "Se requiere al menos una regla. Para deshabilitar reglas, "
                "márquelas con `enabled: false`.",
            )
        path = editable_auto_reject_rules_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rules_yaml.strip() + "\n", encoding="utf-8")
        # No se aplica a publicaciones existentes automáticamente: tras guardar, la UI
        # ofrece aplicarlas de forma explícita y por canal (ver /settings/autoreject/apply).
        return RedirectResponse("/settings/autoreject?saved=1", status_code=303)

    @app.post("/settings/autoreject/apply", response_class=HTMLResponse)
    def apply_autoreject_existing(
        request: Request,
        sources: list[str] = Form(default=[]),
        db: Session = Depends(_get_db),
    ):
        """Aplica las reglas vigentes a las publicaciones existentes de los canales
        seleccionados (acción explícita del usuario, nunca automática)."""
        valid_sources = {channel["source"] for channel in _scan_channels()}
        selected = [source for source in sources if source in valid_sources]
        if not selected:
            return RedirectResponse("/settings/autoreject?applied=0", status_code=303)

        rules = load_auto_reject_rules(config)
        applied = 0
        if rules:
            procesos = (
                db.query(Process)
                .options(joinedload(Process.entity))
                .filter(
                    Process.status == ProcessStatus.publicada,
                    Process.auto_reject_exempt.is_(False),
                    Process.source.in_(selected),
                )
                .all()
            )
            # Savepoint por proceso: el fallo en uno (p. ej. violación de constraint) no
            # debe descartar las decisiones ya aplicadas al resto del lote.
            for proc in procesos:
                savepoint = db.begin_nested()
                try:
                    match = apply_auto_reject_rules(proc, proc.entity, rules)
                    if match is not None:
                        db.flush()
                        record_autoreject_decision(
                            db, proc, rule_id=match.id, reason=proc.auto_reject_reason
                        )
                        applied += 1
                    savepoint.commit()
                except Exception:
                    savepoint.rollback()
                    logger.exception(
                        "Error aplicando autoreject a proceso id=%s", proc.id
                    )
            db.commit()
        return RedirectResponse(
            f"/settings/autoreject?applied={applied}", status_code=303
        )
