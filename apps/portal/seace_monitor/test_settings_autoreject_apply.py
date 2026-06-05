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


def test_apply_isolates_per_item_failure(tmp_path: Path, monkeypatch):
    # Durabilidad batch (review nuevo, Medium): si falla la decisión de un proceso, las
    # decisiones ya aplicadas al resto del lote deben persistir (savepoint por ítem).
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'ar4.db'}")
    app = create_app(cfg)
    db = session_factory()
    try:
        db.add(Entity(id=1, ruc="20100000001", nombre="Entidad SEACE", activa=True))
        db.flush()
        db.add_all(
            [
                Process(
                    entity_id=1, anio=2026, source="seace", source_ref="s1",
                    nid_proceso="s1", nomenclatura="LP-1",
                    objeto="Servicio", descripcion="SERVICIO DE LIMPIEZA A",
                    status=ProcessStatus.publicada,
                ),
                Process(
                    entity_id=1, anio=2026, source="seace", source_ref="s3",
                    nid_proceso="s3", nomenclatura="LP-3",
                    objeto="Servicio", descripcion="SERVICIO DE LIMPIEZA B",
                    status=ProcessStatus.publicada,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    import seace_monitor.web.settings_autoreject as mod

    real = mod.record_autoreject_decision

    def flaky(session, proc, **kwargs):
        if proc.nomenclatura == "LP-1":
            raise RuntimeError("boom en LP-1")
        return real(session, proc, **kwargs)

    monkeypatch.setattr(mod, "record_autoreject_decision", flaky)

    client = TestClient(app)
    client.post(
        "/settings/autoreject",
        data={"rules_yaml": _RULES_YAML, "action": "save"},
        follow_redirects=False,
    )
    resp = client.post(
        "/settings/autoreject/apply",
        data={"sources": ["seace"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/settings/autoreject?applied=1"

    db = session_factory()
    try:
        # LP-1 falló y se revirtió su savepoint → sigue publicada, sin decisión overlay.
        assert _status(db, "LP-1") == ProcessStatus.publicada
        # LP-3 se aplicó y persiste pese al fallo del otro ítem.
        assert _status(db, "LP-3") == ProcessStatus.autorejected
        decisions = db.query(TenantFeedDecision).all()
        assert {d.decision for d in decisions} == {"autorejected"}
        assert len(decisions) == 1
    finally:
        db.close()


def test_apply_skips_already_overlay_autorejected(tmp_path: Path):
    # Forward-compat 0.3c-3: un item ya autorechazado vive en status=publicada con la
    # decisión en el overlay; "Aplicar reglas" no debe re-evaluarlo (cuenta applied=0).
    from .feed import record_autoreject_decision

    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'ar5.db'}")
    app = create_app(cfg)
    db = session_factory()
    try:
        db.add(Entity(id=1, ruc="20100000001", nombre="Entidad SEACE", activa=True))
        db.flush()
        proc = Process(
            entity_id=1, anio=2026, source="seace", source_ref="s1",
            nid_proceso="s1", nomenclatura="LP-1",
            objeto="Servicio", descripcion="SERVICIO DE LIMPIEZA DE OFICINAS",
            status=ProcessStatus.publicada,
        )
        db.add(proc)
        db.flush()
        record_autoreject_decision(db, proc, rule_id="limpieza", reason="limpieza: x")
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    client.post(
        "/settings/autoreject",
        data={"rules_yaml": _RULES_YAML, "action": "save"},
        follow_redirects=False,
    )
    resp = client.post(
        "/settings/autoreject/apply",
        data={"sources": ["seace"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    # No re-evalúa el item ya autorechazado por el overlay.
    assert resp.headers["location"] == "/settings/autoreject?applied=0"
    db = session_factory()
    try:
        # Sigue con una sola decisión (no se duplica) y en status=publicada (no se re-muta).
        assert db.query(TenantFeedDecision).count() == 1
        assert _status(db, "LP-1") == ProcessStatus.publicada
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
