"""Tests de Settings para reglas de autoreject."""

from fastapi.testclient import TestClient

from .auto_reject import editable_auto_reject_rules_path
from .config import AppConfig
from .web.app import create_app


def test_settings_autoreject_shows_default_rules(tmp_path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'settings.db'}")
    app = create_app(cfg)

    response = TestClient(app).get("/settings/autoreject")

    assert response.status_code == 200
    assert "Reglas autoreject" in response.text
    assert "servicio_limpieza" in response.text


def test_settings_autoreject_rejects_invalid_yaml(tmp_path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'settings2.db'}")
    app = create_app(cfg)

    response = TestClient(app).post(
        "/settings/autoreject",
        data={"rules_yaml": "rules: [", "action": "save"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "YAML inválido" in response.text


def test_settings_autoreject_saves_tenant_override(tmp_path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'settings3.db'}")
    app = create_app(cfg)
    rules_yaml = """
rules:
  - id: test_rule
    reason: Prueba
    query: 'objeto:servicio limpieza'
"""

    response = TestClient(app).post(
        "/settings/autoreject",
        data={"rules_yaml": rules_yaml, "action": "save"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/autoreject?saved=1"
    saved = editable_auto_reject_rules_path(cfg)
    assert saved.exists()
    assert "test_rule" in saved.read_text(encoding="utf-8")
