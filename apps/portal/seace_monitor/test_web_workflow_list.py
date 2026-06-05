"""Tests de rutas descargados/analizados (redirect y sort)."""

from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from .config import AppConfig
from .db.models import Entity, InterestStatus, Process, ProcessStatus
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


def _first_table_row_cells(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    row = soup.select_one("table.data tbody tr")
    assert row is not None
    return [" ".join(cell.get_text(" ", strip=True).split()) for cell in row.find_all("td")]


def _table_headers(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [
        " ".join(cell.get_text(" ", strip=True).split())
        for cell in soup.select("table.data thead th")
    ]


def _seed_workflow_list_process(status: ProcessStatus, *, rank_attr: str) -> int:
    db: Session = session_factory()
    try:
        entity = Entity(ruc="20123456789", nombre="ENTIDAD TEST", activa=True)
        db.add(entity)
        db.flush()
        proc = Process(
            entity_id=entity.id,
            anio=2026,
            nid_proceso=f"list-{status.value}",
            nomenclatura="NOM-TEST",
            status=status,
            fecha_publicacion="01/06/2026 10:00",
            objeto="Bien",
            descripcion="Compra de radios",
            cronograma_json=json.dumps(
                [
                    {
                        "etapa": "Presentación de consultas",
                        "fecha_inicio": "02/06/2026 00:00",
                        "fecha_fin": "03/06/2026 23:59",
                    },
                    {
                        "etapa": "Presentación de ofertas",
                        "fecha_inicio": "04/06/2026 00:00",
                        "fecha_fin": "05/06/2026 23:59",
                    },
                ]
            ),
        )
        setattr(proc, rank_attr, 1)
        db.add(proc)
        db.commit()
        return proc.id
    finally:
        db.close()


def test_publicaciones_list_has_no_correlativo_or_numero_column(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'pubs.db'}")
    app = create_app(cfg)
    db: Session = session_factory()
    try:
        entity = Entity(ruc="20123456789", nombre="ENTIDAD TEST", activa=True)
        db.add(entity)
        db.flush()
        db.add(
            Process(
                entity_id=entity.id,
                anio=2026,
                nid_proceso="pub-1",
                numero="17",
                nomenclatura="NOM-PUB",
                status=ProcessStatus.publicada,
                fecha_publicacion="01/06/2026 10:00",
                objeto="Bien",
                descripcion="Compra de radios",
                cuantia="100.00",
                moneda="Soles",
                cronograma_json=json.dumps(
                    [
                        {
                            "etapa": "Presentación de consultas",
                            "fecha_inicio": "02/06/2026 00:00",
                            "fecha_fin": "03/06/2026 23:59",
                        },
                        {
                            "etapa": "Presentación de ofertas",
                            "fecha_inicio": "04/06/2026 00:00",
                            "fecha_fin": "05/06/2026 23:59",
                        },
                    ]
                ),
            )
        )
        db.commit()
    finally:
        db.close()

    response = TestClient(app).get("/publicaciones?sort=correlativo")

    assert response.status_code == 200
    headers = _table_headers(response.text)
    assert headers == [
        "Entidad",
        "Fecha pub.",
        "Nomenclatura",
        "Objeto",
        "Descripción",
        "Cuantía",
        "Moneda",
        "Fin consultas",
        "Fin presentación",
        "Estado",
        "Acciones",
    ]
    cells = _first_table_row_cells(response.text)
    assert cells[:9] == [
        "ENTIDAD TEST",
        "01/06/2026 10:00",
        "NOM-PUB",
        "Bien",
        "Compra de radios",
        "100.00",
        "Soles",
        "03/06/2026 23:59",
        "05/06/2026 23:59",
    ]
    assert cells[9].startswith("publicada")
    assert "Ver en SEACE" in cells[9]
    assert "17" not in cells[:10]


def test_descargados_list_cells_match_headers(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web_list.db'}")
    app = create_app(cfg)
    _seed_workflow_list_process(
        ProcessStatus.descargada,
        rank_attr="list_rank_descargados",
    )

    response = TestClient(app).get("/descargados")

    assert response.status_code == 200
    cells = _first_table_row_cells(response.text)
    assert cells[:8] == [
        "1",
        "01/06/2026 10:00",
        "ENTIDAD TEST",
        "NOM-TEST",
        "Bien",
        "Compra de radios",
        "03/06/2026 23:59",
        "05/06/2026 23:59",
    ]
    assert cells[8].startswith("descargada")
    assert "Ver en SEACE" in cells[8]


def test_analizados_list_cells_match_headers(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web_list2.db'}")
    app = create_app(cfg)
    _seed_workflow_list_process(
        ProcessStatus.analizada,
        rank_attr="list_rank_analizados",
    )

    response = TestClient(app).get("/analizados")

    assert response.status_code == 200
    cells = _first_table_row_cells(response.text)
    assert cells[:8] == [
        "1",
        "01/06/2026 10:00",
        "ENTIDAD TEST",
        "NOM-TEST",
        "Bien",
        "Compra de radios",
        "03/06/2026 23:59",
        "05/06/2026 23:59",
    ]
    assert cells[8].startswith("analizada")
    assert "Ver en SEACE" in cells[8]


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
            auto_reject_reason="servicio_limpieza: Servicios de limpieza fuera de foco",
        )
        db.add(proc)
        db.commit()
    finally:
        db.close()

    response = TestClient(app).get("/descartados")

    assert response.status_code == 200
    assert "AUTO-REJECTED-1" in response.text
    assert "autorejected" in response.text
    assert "servicio_limpieza" in response.text


def test_descartados_can_filter_autorejected_only(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web_filter.db'}")
    app = create_app(cfg)
    db: Session = session_factory()
    try:
        db.add_all(
            [
                Process(
                    entity_id=1,
                    anio=2026,
                    nid_proceso="auto-filter",
                    nomenclatura="AUTO-FILTER",
                    status=ProcessStatus.autorejected,
                    auto_reject_reason="servicio_limpieza: Servicios de limpieza fuera de foco",
                ),
                Process(
                    entity_id=1,
                    anio=2026,
                    nid_proceso="discarded-filter",
                    nomenclatura="DISCARDED-FILTER",
                    status=ProcessStatus.descartada,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    response = TestClient(app).get("/descartados?estado=autorejected")

    assert response.status_code == 200
    assert "AUTO-FILTER" in response.text
    assert "DISCARDED-FILTER" not in response.text
    assert 'href="/descartados?estado=autorejected"' in response.text


def test_restaurar_autorejected_sets_auto_reject_exempt(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web6.db'}")
    app = create_app(cfg)
    db: Session = session_factory()
    try:
        proc = Process(
            entity_id=1,
            anio=2026,
            nid_proceso="auto-2",
            nomenclatura="AUTO-REJECTED-2",
            status=ProcessStatus.autorejected,
            auto_reject_reason="servicio_limpieza: Servicios de limpieza fuera de foco",
        )
        db.add(proc)
        db.commit()
        process_id = proc.id
    finally:
        db.close()

    response = TestClient(app).post(
        f"/descartados/{process_id}/restaurar",
        data={"estado": "autorejected"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/descartados?estado=autorejected"
    db = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        assert proc.status == ProcessStatus.publicada
        assert proc.auto_reject_exempt is True
        assert proc.auto_reject_reason is None
    finally:
        db.close()


def test_descartar_autorejected_marks_process_as_discarded(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'web7.db'}")
    app = create_app(cfg)
    db: Session = session_factory()
    try:
        proc = Process(
            entity_id=1,
            anio=2026,
            nid_proceso="auto-3",
            nomenclatura="AUTO-REJECTED-3",
            status=ProcessStatus.autorejected,
            auto_reject_reason="servicio_limpieza: Servicios de limpieza fuera de foco",
        )
        db.add(proc)
        db.commit()
        process_id = proc.id
    finally:
        db.close()

    response = TestClient(app).post(
        f"/descartados/{process_id}/descartar",
        data={"estado": "autorejected"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/descartados?estado=autorejected"
    db = session_factory()
    try:
        proc = db.get(Process, process_id)
        assert proc is not None
        assert proc.status == ProcessStatus.descartada
        assert proc.auto_reject_reason is None
    finally:
        db.close()


def _seed_overlay_autorejected(tmp_path: Path, db_name: str):
    """Item en régimen post-0.3c-3: status=publicada pero overlay=autorejected."""
    from .feed import record_autoreject_decision

    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / db_name}")
    app = create_app(cfg)
    db: Session = session_factory()
    try:
        entity = Entity(ruc="20999999999", nombre="ENTIDAD OVERLAY", activa=True)
        db.add(entity)
        db.flush()
        proc = Process(
            entity_id=entity.id,
            anio=2026,
            source="seace",
            nid_proceso="overlay-1",
            nomenclatura="OVERLAY-AUTO-1",
            status=ProcessStatus.publicada,
            objeto="Servicio",
            descripcion="Servicio fuera de foco",
        )
        db.add(proc)
        db.flush()
        record_autoreject_decision(db, proc, rule_id="r", reason="r: fuera de foco")
        db.commit()
        return app, proc.id
    finally:
        db.close()


def test_overlay_autorejected_excluded_from_publicaciones(tmp_path: Path):
    app, _ = _seed_overlay_autorejected(tmp_path, "ov_pub.db")
    response = TestClient(app).get("/publicaciones")
    assert response.status_code == 200
    assert "OVERLAY-AUTO-1" not in response.text


def test_overlay_autorejected_shown_in_descartados(tmp_path: Path):
    app, _ = _seed_overlay_autorejected(tmp_path, "ov_desc.db")
    # Default y filtro autoreject lo incluyen; filtro descartada manual no.
    assert "OVERLAY-AUTO-1" in TestClient(app).get("/descartados").text
    assert (
        "OVERLAY-AUTO-1"
        in TestClient(app).get("/descartados?estado=autorejected").text
    )
    assert (
        "OVERLAY-AUTO-1"
        not in TestClient(app).get("/descartados?estado=descartada").text
    )


def test_overlay_autorejected_counts_in_dashboard(tmp_path: Path):
    app, _ = _seed_overlay_autorejected(tmp_path, "ov_dash.db")
    html = TestClient(app).get("/").text
    soup = BeautifulSoup(html, "lxml")

    # El único item está en status=publicada pero overlay=autorejected: la tarjeta
    # "Publicadas" no debe contarlo.
    publicadas_card = None
    for card in soup.select(".card"):
        label = card.select_one(".label")
        if label and label.get_text(strip=True) == "Publicadas":
            publicadas_card = card
            break
    assert publicadas_card is not None
    assert publicadas_card.select_one(".num").get_text(strip=True) == "0"

    # Tampoco debe aparecer en "Últimas publicaciones detectadas".
    assert "OVERLAY-AUTO-1" not in html


def test_overlay_autorejected_can_be_restored_and_discarded(tmp_path: Path):
    app, pid = _seed_overlay_autorejected(tmp_path, "ov_act.db")
    # Descartar definitivo debe permitirse (es efectivamente autorejected).
    resp = TestClient(app).post(
        f"/descartados/{pid}/descartar",
        data={"estado": "autorejected"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db = session_factory()
    try:
        proc = db.get(Process, pid)
        assert proc.status == ProcessStatus.descartada
    finally:
        db.close()


def test_overlay_autorejected_descartar_from_publicaciones_redirects(tmp_path: Path):
    # Guard 0.3c-3: descartar desde Publicaciones un autorechazado efectivo (status=
    # publicada, oculto) no debe marcarlo descartada; redirige a Descartados.
    app, pid = _seed_overlay_autorejected(tmp_path, "ov_desc_guard.db")
    resp = TestClient(app).post(
        f"/publicaciones/{pid}/descartar", data={}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/descartados?estado=autorejected"
    db = session_factory()
    try:
        assert db.get(Process, pid).status == ProcessStatus.publicada
    finally:
        db.close()


def test_overlay_autorejected_descargar_redirects(tmp_path: Path):
    app, pid = _seed_overlay_autorejected(tmp_path, "ov_dl_guard.db")
    resp = TestClient(app).post(
        f"/publicaciones/{pid}/descargar", data={}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/descartados?estado=autorejected"
    db = session_factory()
    try:
        # No entra al pipeline de descarga.
        assert db.get(Process, pid).status == ProcessStatus.publicada
    finally:
        db.close()


def test_descartar_autorejected_clears_all_tenant_overlay(tmp_path: Path):
    # Medium/Low: descartar (status compartido) limpia overlay de TODOS los tenants y
    # resetea auto_reject_exempt.
    from .db.models import TenantFeedDecision

    app, pid = _seed_overlay_autorejected(tmp_path, "ov_desc_all.db")
    db = session_factory()
    try:
        db.add(
            TenantFeedDecision(tenant_id="otro", feed_item_id=pid, decision="exempt")
        )
        db.commit()
        assert db.query(TenantFeedDecision).filter_by(feed_item_id=pid).count() == 2
    finally:
        db.close()

    resp = TestClient(app).post(
        f"/descartados/{pid}/descartar",
        data={"estado": "autorejected"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db = session_factory()
    try:
        proc = db.get(Process, pid)
        assert proc.status == ProcessStatus.descartada
        assert proc.auto_reject_exempt is False
        assert db.query(TenantFeedDecision).filter_by(feed_item_id=pid).count() == 0
    finally:
        db.close()


def test_restore_promotes_item(tmp_path: Path):
    # 0.3d (review): restaurar marca promoted_at para que el item (y su exempt) no se borre
    # como duplicado publicada en una re-publicación SEACE.
    app, pid = _seed_overlay_autorejected(tmp_path, "ov_restore_promo.db")
    resp = TestClient(app).post(
        f"/descartados/{pid}/restaurar",
        data={"estado": "autorejected"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db = session_factory()
    try:
        proc = db.get(Process, pid)
        assert proc.status == ProcessStatus.publicada
        assert proc.auto_reject_exempt is True
        assert proc.promoted_at is not None
    finally:
        db.close()


def test_overlay_autorejected_restore_sets_exempt(tmp_path: Path):
    app, pid = _seed_overlay_autorejected(tmp_path, "ov_restore.db")
    resp = TestClient(app).post(
        f"/descartados/{pid}/restaurar",
        data={"estado": "autorejected"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db = session_factory()
    try:
        proc = db.get(Process, pid)
        assert proc.auto_reject_exempt is True
    finally:
        db.close()
