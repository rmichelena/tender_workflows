"""Tests del editor de reglas: aplicar a publicaciones existentes por canal (2b)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Entity, Process, ProcessStatus, TenantFeedDecision
from .db.session import session_factory
from .web.app import create_app

_RULES_YAML = "rules:\n  - id: limpieza\n    query: limpieza\n    reason: limpieza fuera de foco\n"


def _seed(db: Session) -> None:
    db.add(Entity(id=1, ruc="20100000001", nombre="Entidad SEACE", activa=True))
    db.add(Entity(id=2, ruc="ADP-PORTAL", nombre="ADP", activa=True))
    db.flush()
    db.add_all(
        [
            Process(
                entity_id=1, anio=2026, source="seace", source_ref="s1",
                nid_proceso="s1", nomenclatura="LP-1",
                objeto="Servicio", descripcion="SERVICIO DE LIMPIEZA DE OFICINAS",
                status=ProcessStatus.publicada,
            ),
            Process(
                entity_id=1, anio=2026, source="seace", source_ref="s2",
                nid_proceso="s2", nomenclatura="LP-2",
                objeto="Bien", descripcion="EQUIPOS DE RAYOS X",
                status=ProcessStatus.publicada,
            ),
            Process(
                entity_id=2, anio=2026, source="adp_portal", source_ref="a1",
                nomenclatura="ADP-1",
                objeto="Servicios", descripcion="SERVICIO DE LIMPIEZA INTEGRAL",
                status=ProcessStatus.publicada,
            ),
        ]
    )
    db.commit()


def _status(db, nomenclatura):
    return (
        db.query(Process).filter(Process.nomenclatura == nomenclatura).one().status
    )


def test_saving_rules_does_not_autoapply_to_existing(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'ar.db'}")
    app = create_app(cfg)
    db = session_factory()
    try:
        _seed(db)
    finally:
        db.close()

    client = TestClient(app)
    resp = client.post(
        "/settings/autoreject",
        data={"rules_yaml": _RULES_YAML, "action": "save"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings/autoreject?saved=1"

    db = session_factory()
    try:
        # Guardar NO debe autorechazar publicaciones existentes que matchean.
        assert _status(db, "LP-1") == ProcessStatus.publicada
        assert _status(db, "ADP-1") == ProcessStatus.publicada
        assert db.query(TenantFeedDecision).count() == 0
    finally:
        db.close()


def test_apply_existing_only_selected_channel(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'ar2.db'}")
    app = create_app(cfg)
    db = session_factory()
    try:
        _seed(db)
    finally:
        db.close()

    client = TestClient(app)
    client.post(
        "/settings/autoreject",
        data={"rules_yaml": _RULES_YAML, "action": "save"},
        follow_redirects=False,
    )
    # Aplica solo al canal SEACE.
    resp = client.post(
        "/settings/autoreject/apply",
        data={"sources": ["seace"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings/autoreject?applied=1"

    db = session_factory()
    try:
        assert _status(db, "LP-1") == ProcessStatus.autorejected  # matchea + canal seace
        assert _status(db, "LP-2") == ProcessStatus.publicada  # no matchea
        assert _status(db, "ADP-1") == ProcessStatus.publicada  # canal no seleccionado
        # Doble escritura al overlay.
        decisions = db.query(TenantFeedDecision).all()
        assert len(decisions) == 1
        assert decisions[0].decision == "autorejected"
        assert decisions[0].rule_id == "limpieza"
    finally:
        db.close()


def test_apply_with_no_channels_selected_is_noop(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'ar3.db'}")
    app = create_app(cfg)
    db = session_factory()
    try:
        _seed(db)
    finally:
        db.close()

    client = TestClient(app)
    client.post(
        "/settings/autoreject",
        data={"rules_yaml": _RULES_YAML, "action": "save"},
        follow_redirects=False,
    )
    resp = client.post(
        "/settings/autoreject/apply", data={}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings/autoreject?applied=0"

    db = session_factory()
    try:
        assert _status(db, "LP-1") == ProcessStatus.publicada
    finally:
        db.close()
