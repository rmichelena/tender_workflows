"""Tests de re-análisis y rollback del resultado previo."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .analysis.runner import AnalysisRunner
from .client import ProcessRow
from .config import AppConfig
from .db.models import AnalysisResult, Base, Entity, FeedItem, ProcessStatus
from .parser import CronogramaEtapa, Documento, FichaData


@contextmanager
def _noop_analysis_lock(_path):
    yield


@pytest.fixture
def analysis_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20100000001", nombre="Test", activa=True)
    session.add(entity)
    session.flush()
    yield session
    session.close()


def test_analyze_restores_done_analysis_when_prior_snapshot_provided(
    analysis_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc_dir = tmp_path / "proc"
    docs_dir = proc_dir / "documentos"
    docs_dir.mkdir(parents=True)
    doc = docs_dir / "bases.pdf"
    doc.write_bytes(b"pdf")
    (docs_dir / "manifest.json").write_text(
        json.dumps(
            [{"uuid": "u1", "nombre": "bases.pdf", "archivo": "bases.pdf"}]
        ),
        encoding="utf-8",
    )
    proc = FeedItem(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="nid-1",
        nomenclatura="T-1",
        status=ProcessStatus.analizada,
        documentos_json=json.dumps([{"uuid": "u1", "nombre": "bases.pdf"}]),
        data_dir=str(proc_dir),
    )
    analysis_session.add(proc)
    analysis_session.flush()
    analysis = AnalysisResult(
        process_id=proc.id,
        status="done",
        alcance="Alcance previo",
        run_id="old-run",
    )
    analysis_session.add(analysis)
    analysis_session.commit()

    prior = AnalysisRunner._analysis_snapshot(analysis)
    AnalysisRunner._mark_analysis_running(analysis, "new-run")
    proc.status = ProcessStatus.descargada
    analysis_session.commit()

    runner = AnalysisRunner(cfg, analysis_session)
    with patch.object(
        runner, "_run_pipeline", side_effect=RuntimeError("pipeline failed")
    ):
        with pytest.raises(RuntimeError, match="pipeline failed"):
            runner.analyze(
                proc.id,
                ["bases.pdf"],
                run_id="new-run",
                prior_snapshot=prior,
            )

    analysis_session.refresh(analysis)
    analysis_session.refresh(proc)
    assert analysis.status == "done"
    assert analysis.alcance == "Alcance previo"
    assert proc.status == ProcessStatus.analizada


def _setup_descargado_process(
    analysis_session: Session, tmp_path: Path, *, nid: str = "nid-1"
) -> tuple[AppConfig, FeedItem]:
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc_dir = tmp_path / nid
    docs_dir = proc_dir / "documentos"
    docs_dir.mkdir(parents=True)
    (docs_dir / "bases.pdf").write_bytes(b"pdf")
    (docs_dir / "manifest.json").write_text(
        json.dumps(
            [{"uuid": "u1", "nombre": "bases.pdf", "archivo": "bases.pdf"}]
        ),
        encoding="utf-8",
    )
    proc = FeedItem(
        entity_id=entity.id,
        anio=2026,
        nid_proceso=nid,
        nomenclatura=f"T-{nid}",
        status=ProcessStatus.descargada,
        documentos_json=json.dumps([{"uuid": "u1", "nombre": "bases.pdf"}]),
        data_dir=str(proc_dir),
    )
    analysis_session.add(proc)
    analysis_session.flush()
    return cfg, proc


def test_analyze_success_assigns_analizados_rank(
    analysis_session: Session, tmp_path: Path
):
    cfg, proc = _setup_descargado_process(analysis_session, tmp_path)
    runner = AnalysisRunner(cfg, analysis_session)
    with patch("seace_monitor.analysis.runner.analysis_lock", _noop_analysis_lock):
        with patch.object(
            runner,
            "_run_pipeline",
            return_value={"stage1": {"mode": "fast_gemini", "alcance": "X"}},
        ):
            runner.analyze(proc.id, ["bases.pdf"])

    analysis_session.refresh(proc)
    assert proc.status == ProcessStatus.analizada
    assert proc.list_rank_analizados == 1
    assert proc.list_rank_descargados is None


def test_rerun_leaves_and_reenters_analizados_without_duplicate_ranks(
    analysis_session: Session, tmp_path: Path
):
    cfg, p1 = _setup_descargado_process(analysis_session, tmp_path, nid="p1")
    _, p2 = _setup_descargado_process(analysis_session, tmp_path, nid="p2")
    p1.status = ProcessStatus.analizada
    p1.list_rank_analizados = 1
    p2.status = ProcessStatus.analizada
    p2.list_rank_analizados = 2
    analysis_session.add(
        AnalysisResult(process_id=p1.id, status="done", alcance="A1")
    )
    analysis_session.add(
        AnalysisResult(process_id=p2.id, status="done", alcance="A2")
    )
    analysis_session.commit()

    runner = AnalysisRunner(cfg, analysis_session)
    with patch("seace_monitor.analysis.runner.analysis_lock", _noop_analysis_lock):
        with patch.object(
            runner,
            "_run_pipeline",
            return_value={"stage1": {"mode": "fast_gemini", "alcance": "A1b"}},
        ):
            runner.analyze(p1.id, ["bases.pdf"])

    analysis_session.refresh(p1)
    analysis_session.refresh(p2)
    assert p1.list_rank_analizados == 2
    assert p2.list_rank_analizados == 1
    assert {p1.list_rank_analizados, p2.list_rank_analizados} == {1, 2}


def test_analysis_snapshot_includes_run_id_for_rollback():
    analysis = AnalysisResult(status="done", run_id="run-abc")
    snap = AnalysisRunner._analysis_snapshot(analysis)
    assert snap["run_id"] == "run-abc"


def test_download_fetches_documents_with_current_row_from_later_page(
    analysis_session: Session,
):
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc = FeedItem(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="target-nid",
        nomenclatura="T-target",
        status=ProcessStatus.publicada,
        nid_convocatoria="old-conv",
        link_id="old-link",
    )
    analysis_session.add(proc)
    analysis_session.flush()
    fresh_row = ProcessRow(
        row_index=0,
        numero="",
        fecha_publicacion="",
        nomenclatura="T-target",
        reiniciado_desde="",
        objeto="",
        descripcion="",
        cuantia="",
        moneda="",
        version_seace="",
        nid_proceso="target-nid",
        nid_convocatoria="fresh-conv",
        nid_sistema="3",
        link_id="fresh-link",
        ntipo="0",
    )
    first_soup = object()
    second_soup = object()
    mock_client = MagicMock()
    mock_client.fetch_list_page.side_effect = [("", first_soup), ("", second_soup)]
    mock_client.total_pages.return_value = 2
    mock_client.parse_rows.side_effect = [[], [fresh_row]]
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://x", ficha_id="f1"
    )
    ficha = FichaData(
        ficha_id="f1",
        nid_proceso="target-nid",
        nomenclatura="T-target",
        descripcion="",
        objeto="",
        fecha_publicacion="",
        documentos=[Documento("u1", "bases.pdf", "", "", "", "", "3")],
    )

    runner = AnalysisRunner(cfg, analysis_session)
    with (
        patch("seace_monitor.seace_search.SeaceClient", return_value=mock_client),
        patch("seace_monitor.analysis.runner.parse_ficha", return_value=ficha),
    ):
        docs = runner._fetch_documentos_from_seace(proc, entity.ruc)

    assert docs[0]["uuid"] == "u1"
    mock_client.open_ficha.assert_called_once_with(fresh_row)
    assert proc.link_id == "fresh-link"
    assert proc.nid_convocatoria == "fresh-conv"


def test_download_persists_cronograma_from_ficha(
    analysis_session: Session,
):
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc = FeedItem(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="target-nid",
        nomenclatura="T-target",
        status=ProcessStatus.publicada,
        nid_convocatoria="old-conv",
        link_id="old-link",
    )
    analysis_session.add(proc)
    analysis_session.flush()
    row = ProcessRow(
        row_index=0,
        numero="",
        fecha_publicacion="01/01/2026 10:00",
        nomenclatura="T-target",
        reiniciado_desde="",
        objeto="Bien",
        descripcion="Compra",
        cuantia="",
        moneda="Soles",
        version_seace="3",
        nid_proceso="target-nid",
        nid_convocatoria="fresh-conv",
        nid_sistema="3",
        link_id="fresh-link",
        ntipo="0",
    )
    mock_client = MagicMock()
    mock_client.fetch_list_page.return_value = ("", object())
    mock_client.total_pages.return_value = 1
    mock_client.parse_rows.return_value = [row]
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://seace/ficha", ficha_id="f1"
    )
    ficha = FichaData(
        ficha_id="f1",
        nid_proceso="target-nid",
        nomenclatura="T-target",
        descripcion="Compra",
        objeto="Bien",
        fecha_publicacion="01/01/2026 10:00",
        cronograma=[
            CronogramaEtapa(
                "Formulación de consultas y observaciones",
                "02/01/2026 00:01",
                "03/01/2026 23:59",
            ),
            CronogramaEtapa(
                "Presentación de ofertas",
                "04/01/2026 00:01",
                "05/01/2026 23:59",
            ),
        ],
        documentos=[Documento("u1", "bases.pdf", "", "", "", "", "3")],
    )

    runner = AnalysisRunner(cfg, analysis_session)
    with (
        patch("seace_monitor.seace_search.SeaceClient", return_value=mock_client),
        patch("seace_monitor.analysis.runner.parse_ficha", return_value=ficha),
    ):
        docs = runner._fetch_documentos_from_seace(proc, entity.ruc)

    assert docs[0]["uuid"] == "u1"
    cronograma = json.loads(proc.cronograma_json or "[]")
    assert len(cronograma) == 2
    assert proc.fecha_consultas == "03/01/2026 23:59"
    assert proc.fecha_presentacion == "05/01/2026 23:59"
    assert proc.fecha_publicacion == "01/01/2026 10:00"
    assert proc.ficha_id == "f1"
    assert proc.ficha_url == "http://seace/ficha"
    assert proc.content_hash == ficha.content_hash()


def test_download_uses_continued_process_row_matched_by_nomenclatura(
    analysis_session: Session,
):
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc = FeedItem(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="old-nid",
        nomenclatura="LP-ABR-7-2026-BCRPLIM-2",
        status=ProcessStatus.publicada,
        nid_convocatoria="old-conv",
        link_id="old-link",
    )
    analysis_session.add(proc)
    analysis_session.flush()
    continued_row = ProcessRow(
        row_index=0,
        numero="",
        fecha_publicacion="",
        nomenclatura="LP-ABR-7-2026-BCRPLIM-2",
        reiniciado_desde="",
        objeto="",
        descripcion="",
        cuantia="",
        moneda="",
        version_seace="",
        nid_proceso="new-nid",
        nid_convocatoria="fresh-conv",
        nid_sistema="3",
        link_id="fresh-link",
        ntipo="0",
    )
    mock_client = MagicMock()
    mock_client.fetch_list_page.return_value = ("", object())
    mock_client.total_pages.return_value = 1
    mock_client.parse_rows.return_value = [continued_row]
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://x", ficha_id="f1"
    )
    ficha = FichaData(
        ficha_id="f1",
        nid_proceso="new-nid",
        nomenclatura="LP-ABR-7-2026-BCRPLIM-2",
        descripcion="",
        objeto="",
        fecha_publicacion="",
        documentos=[Documento("u1", "bases.pdf", "", "", "", "", "3")],
    )

    runner = AnalysisRunner(cfg, analysis_session)
    with (
        patch("seace_monitor.seace_search.SeaceClient", return_value=mock_client),
        patch("seace_monitor.analysis.runner.parse_ficha", return_value=ficha),
    ):
        docs = runner._fetch_documentos_from_seace(proc, entity.ruc)

    assert docs[0]["uuid"] == "u1"
    mock_client.open_ficha.assert_called_once_with(continued_row)
    assert proc.nid_proceso == "new-nid"
    assert proc.link_id == "fresh-link"


def test_download_commits_before_and_after_seace_fetch(analysis_session: Session):
    cfg = AppConfig()
    entity = analysis_session.query(Entity).one()
    proc = FeedItem(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="target-nid",
        nomenclatura="T-target",
        status=ProcessStatus.descargando,
        nid_convocatoria="old-conv",
        link_id="old-link",
    )
    analysis_session.add(proc)
    analysis_session.flush()
    proc.entity = entity

    row = ProcessRow(
        row_index=0,
        numero="",
        fecha_publicacion="",
        nomenclatura="T-target",
        reiniciado_desde="",
        objeto="",
        descripcion="",
        cuantia="",
        moneda="",
        version_seace="",
        nid_proceso="target-nid",
        nid_convocatoria="fresh-conv",
        nid_sistema="3",
        link_id="fresh-link",
        ntipo="0",
    )
    ficha_result = MagicMock(html="<html>", url="http://x", ficha_id="f1")
    ficha = FichaData(
        ficha_id="f1",
        nid_proceso="target-nid",
        nomenclatura="T-target",
        descripcion="",
        objeto="",
        fecha_publicacion="",
        documentos=[Documento("u1", "bases.pdf", "", "", "", "", "3")],
    )
    order: list[str] = []
    real_commit = analysis_session.commit

    def track_commit() -> None:
        order.append("commit")
        real_commit()

    analysis_session.commit = track_commit  # type: ignore[method-assign]

    def seace_fetch(*_args, **_kwargs):
        order.append("seace")
        return row, ficha_result, MagicMock()

    runner = AnalysisRunner(cfg, analysis_session)
    with (
        patch("seace_monitor.analysis.runner.open_ficha_for_process", side_effect=seace_fetch),
        patch("seace_monitor.analysis.runner.parse_ficha", return_value=ficha),
        patch.object(runner, "_fetch_documents"),
    ):
        runner.download(proc.id)

    assert order[:2] == ["commit", "seace"]
    assert order.count("commit") >= 2
    assert proc.nid_convocatoria == "fresh-conv"
