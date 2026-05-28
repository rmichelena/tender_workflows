"""Rutas de configuración de reglas autoreject."""

from __future__ import annotations

import yaml
from fastapi import Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auto_reject import (
    active_auto_reject_rules_path,
    editable_auto_reject_rules_path,
    validate_rules_yaml,
)
from ..config import AppConfig


def _rules_text(config: AppConfig) -> tuple[str, bool]:
    active = active_auto_reject_rules_path(config)
    return active.read_text(encoding="utf-8"), active == editable_auto_reject_rules_path(config)


def register_autoreject_settings_routes(app, config: AppConfig, render, _get_db):
    @app.get("/settings/autoreject", response_class=HTMLResponse)
    def settings_autoreject(request: Request, saved: str | None = None):
        text, using_override = _rules_text(config)
        return render(
            request,
            "settings_autoreject.html",
            active_page="settings_autoreject",
            rules_yaml=text,
            using_override=using_override,
            saved=bool(saved),
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
            raise HTTPException(400, "YAML inválido: al menos una regla es requerida")
        path = editable_auto_reject_rules_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rules_yaml.strip() + "\n", encoding="utf-8")
        return RedirectResponse("/settings/autoreject?saved=1", status_code=303)
