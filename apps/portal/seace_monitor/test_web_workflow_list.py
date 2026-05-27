"""Tests de rutas descargados/analizados (redirect y sort)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import InterestStatus, Process, ProcessStatus
from .db.session import session_factory
from .web.app import create_app


def _seed_analizado(tmp_path: Path) -> int:
    db: Session = session_factory()
    try:
        proc = Process(
            entity_id=1,
            anio=2026,
            nid_proceso="web-1",
            nomenclatura="WEB-1",
            status=ProcessStatus.analizada,
            list_rank_analizados=1,
        )
        db.add(proc)
        db.commit()
        return proc.id
    finally:
        db.close()


def test_cambiar_estado_preserves_sort_and_scroll(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web.db'}")
    app = create_app(cfg)
    process_id = _seed_analizado(tmp_path)

    client = TestClient(app)
    response = client.post(
        f"/analizados/{process_id}/estado",
        data={
            "estado": "portafolio",
            "sort": "fecha_publicacion",
            "dir": "desc",
            "scroll": "120",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "sort=fecha_publicacion" in location
    assert "dir=desc" in location
    assert "scroll=120" in location


def test_descartar_analizado_accepts_sort_dir(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web2.db'}")
    app = create_app(cfg)
    process_id = _seed_analizado(tmp_path)

    client = TestClient(app)
    response = client.post(
        f"/analizados/{process_id}/descartar",
        data={
            "sort": "entidad",
            "dir": "asc",
            "scroll": "50",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "sort=entidad" in location
    assert "dir=asc" in location
    assert "scroll=50" in location


def test_update_interest_status_preserves_list_context(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web3.db'}")
    app = create_app(cfg)
    process_id = _seed_analizado(tmp_path)

    client = TestClient(app)
    response = client.post(
        f"/processes/{process_id}/interest",
        data={
            "interest_status": "candidate",
            "return_to": "/analizados",
            "sort": "fecha_publicacion",
            "dir": "desc",
            "scroll": "80",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/analizados?sort=fecha_publicacion&dir=desc&scroll=80"
    db = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        assert proc.interest_status == InterestStatus.candidate
    finally:
        db.close()


def test_analizados_list_shows_interest_status(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web4.db'}")
    app = create_app(cfg)
    process_id = _seed_analizado(tmp_path)
    db = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        proc.interest_status = InterestStatus.opportunity
        db.commit()
    finally:
        db.close()

    response = TestClient(app).get("/analizados")

    assert response.status_code == 200
    assert "opportunity" in response.text


def test_descartados_includes_autorejected_processes(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web5.db'}")
    app = create_app(cfg)
    db: Session = session_factory()
    try:
        proc = Process(
            entity_id=1,
            anio=2026,
            nid_proceso="auto-1",
            nomenclatura="AUTO-REJECTED-1",
            status=ProcessStatus.autorejected,
        )
        db.add(proc)
        db.commit()
    finally:
        db.close()

    response = TestClient(app).get("/descartados")

    assert response.status_code == 200
    assert "AUTO-REJECTED-1" in response.text
    assert "autorejected" in response.text
