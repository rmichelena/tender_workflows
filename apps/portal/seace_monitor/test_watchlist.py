"""Tests watchlist SEACE."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .analysis.analysis_history import archive_analysis_before_rerun
from .config import AppConfig
from .db.models import AnalysisResult, Base, Entity, Process, ProcessStatus
from .parser import CronogramaEtapa, Documento, FichaData
from .watchlist import (
    _refresh_watchlist_process,
    mark_watchlist_read,
    refresh_watchlist_processes,
    watchlist_fingerprint,
)
from .web.detail_data import parse_cronograma


def test_watchlist_fingerprint_detects_document_change():
    cron = json.dumps([{"etapa": "A", "fecha_inicio": "1", "fecha_fin": "2"}])
    docs_a = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}])
    docs_b = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}, {"uuid": "u2", "nombre": "b.pdf"}])
    fp_a = watchlist_fingerprint(cronograma_json=cron, documentos_json=docs_a)
    fp_b = watchlist_fingerprint(cronograma_json=cron, documentos_json=docs_b)
    assert fp_a != fp_b


def test_parse_cronograma_diff():
    current = json.dumps(
        [
            {"etapa": "Consultas", "fecha_inicio": "01/01/26", "fecha_fin": "10/01/26"},
        ]
    )
    prev = json.dumps(
        [
            {"etapa": "Consultas", "fecha_inicio": "01/01/26", "fecha_fin": "05/01/26"},
        ]
    )
    rows = parse_cronograma(current, prev_cronograma_json=prev)
    assert len(rows) == 1
    assert rows[0].changed is True
    assert rows[0].fecha_fin_changed is True
    assert rows[0].fecha_fin_prev == "05/01/26"
    assert rows[0].fecha_fin == "10/01/26"
    assert rows[0].fecha_inicio_changed is False


def test_parse_cronograma_new_and_removed_stages():
    current = json.dumps(
        [
            {"etapa": "A", "fecha_inicio": "1", "fecha_fin": "2"},
            {"etapa": "C", "fecha_inicio": "5", "fecha_fin": "6"},
        ]
    )
    prev = json.dumps(
        [
            {"etapa": "A", "fecha_inicio": "1", "fecha_fin": "2"},
            {"etapa": "B", "fecha_inicio": "3", "fecha_fin": "4"},
        ]
    )
    rows = parse_cronograma(current, prev_cronograma_json=prev)
    by_etapa = {row.etapa: row for row in rows}
    assert by_etapa["A"].changed is False
    assert by_etapa["C"].is_new is True
    assert by_etapa["C"].changed is True
    assert by_etapa["B"].is_removed is True
    assert by_etapa["B"].fecha_fin_prev == "4"


def test_mark_watchlist_read_clears_flag():
    proc = Process(
        entity_id=1,
        anio=2026,
        nid_proceso="1",
        nomenclatura="T",
        status=ProcessStatus.descargada,
        watch_unread=True,
        watch_cronograma_prev_json="[]",
        watch_documentos_prev_json="[]",
    )
    mark_watchlist_read(proc)
    assert proc.watch_unread is False
    assert proc.watch_cronograma_prev_json is None


def test_analysis_history_paths_are_unique(tmp_path: Path):
    analysis = AnalysisResult(status="done", alcance="x")
    p1 = archive_analysis_before_rerun(tmp_path, analysis)
    p2 = archive_analysis_before_rerun(tmp_path, analysis)
    assert p1 is not None and p2 is not None
    assert p1 != p2
    assert p1.is_dir() and p2.is_dir()


@pytest.fixture
def watch_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20100000001", nombre="Test", activa=True)
    session.add(entity)
    session.flush()
    yield session
    session.close()


def _sample_process(
    session: Session,
    *,
    tmp_path: Path,
    docs: list[dict],
    watch_unread: bool = False,
    prev_docs_json: str | None = None,
) -> Process:
    entity = session.query(Entity).one()
    proc_dir = tmp_path / "proc"
    proc_dir.mkdir(parents=True)
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="nid-1",
        nomenclatura="T-1",
        status=ProcessStatus.descargada,
        nid_convocatoria="conv",
        link_id="link",
        cronograma_json=json.dumps(
            [{"etapa": "A", "fecha_inicio": "1", "fecha_fin": "2"}]
        ),
        documentos_json=json.dumps(docs),
        data_dir=str(proc_dir),
        watch_unread=watch_unread,
        watch_documentos_prev_json=prev_docs_json,
    )
    session.add(proc)
    session.flush()
    proc.entity = entity
    return proc


def _ficha_with_docs(docs: list[Documento]) -> FichaData:
    return FichaData(
        ficha_id="f1",
        nid_proceso="nid-1",
        nomenclatura="T-1",
        descripcion="d",
        objeto="o",
        fecha_publicacion="01/01/26",
        cronograma=[CronogramaEtapa("A", "1", "2")],
        documentos=docs,
    )


def test_refresh_preserves_prev_baseline_while_unread(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    baseline = json.dumps([{"uuid": "u1", "nombre": "a.pdf"}])
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf"}],
        watch_unread=True,
        prev_docs_json=baseline,
    )
    new_docs = [
        Documento("u1", "a.pdf", "", "", "", "", "3"),
        Documento("u2", "b.pdf", "", "", "", "", "3"),
    ]
    ficha = _ficha_with_docs(new_docs)
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(html="<html>", url="http://x", ficha_id="f1")

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document"),
    ):
        assert _refresh_watchlist_process(cfg, watch_session, proc) is True

    assert proc.watch_documentos_prev_json == baseline


def test_refresh_rollback_on_download_failure(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    old_docs = [{"uuid": "u1", "nombre": "a.pdf", "tipo_descarga": "3"}]
    proc = _sample_process(watch_session, tmp_path=tmp_path, docs=old_docs)
    old_json = proc.documentos_json
    new_docs = [
        Documento("u1", "a.pdf", "", "", "", "", "3"),
        Documento("u2", "b.pdf", "", "", "", "", "3"),
    ]
    ficha = _ficha_with_docs(new_docs)
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(html="<html>", url="http://x", ficha_id="f1")

    def _fail_u2(_uuid, dest, **kwargs):
        if "u2" in str(dest) or dest.name.startswith("b"):
            raise OSError("download failed")

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document", side_effect=_fail_u2),
    ):
        updated = refresh_watchlist_processes(cfg, watch_session)

    watch_session.commit()
    watch_session.refresh(proc)
    assert updated == 0
    assert proc.documentos_json == old_json
    assert proc.watch_checked_at is None


def test_refresh_advances_checked_at_on_success(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf", "tipo_descarga": "3"}],
    )
    ficha = _ficha_with_docs([Documento("u1", "a.pdf", "", "", "", "", "3")])

    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(html="<html>", url="http://x", ficha_id="f1")

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document"),
    ):
        refresh_watchlist_processes(cfg, watch_session)

    watch_session.commit()
    watch_session.refresh(proc)
    assert proc.watch_checked_at is not None
