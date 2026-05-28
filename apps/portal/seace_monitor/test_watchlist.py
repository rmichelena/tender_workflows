"""Tests watchlist SEACE."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .analysis.analysis_history import archive_analysis_before_rerun
from .client import ProcessRow
from .config import AppConfig
from .db.models import AnalysisResult, Base, Entity, Process, ProcessStatus
from .parser import CronogramaEtapa, Documento, FichaData
from .watchlist import (
    _refresh_watchlist_process,
    _resolve_current_row,
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


def _row(nid: str, *, link_id: str = "link") -> ProcessRow:
    return ProcessRow(
        row_index=0,
        numero="",
        fecha_publicacion="",
        nomenclatura="T",
        reiniciado_desde="",
        objeto="",
        descripcion="",
        cuantia="",
        moneda="",
        version_seace="",
        nid_proceso=nid,
        nid_convocatoria="conv-fresh",
        nid_sistema="3",
        link_id=link_id,
        ntipo="0",
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

    def _download(docs_dir, doc, **kwargs):
        path = docs_dir / f"{doc['uuid']}.pdf"
        path.write_bytes(b"pdf")
        doc["archivo"] = path.name
        return doc["uuid"] == "u2"

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document", side_effect=_download),
    ):
        assert _refresh_watchlist_process(cfg, watch_session, proc) is True

    assert proc.watch_documentos_prev_json == baseline


def test_watchlist_resolves_current_row_across_list_pages(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf"}],
    )
    proc.nid_proceso = "target-nid"
    first_soup = object()
    second_soup = object()
    current_row = _row("target-nid", link_id="fresh-link")
    client = MagicMock()
    client.fetch_list_page.side_effect = [("", first_soup), ("", second_soup)]
    client.total_pages.return_value = 2
    client.parse_rows.side_effect = [
        [_row("other-nid")],
        [current_row],
    ]

    row = _resolve_current_row(cfg, client, proc)

    assert row is current_row
    assert client.fetch_list_page.call_args_list[0].args == (0,)
    assert client.fetch_list_page.call_args_list[1].args == (1,)


def test_refresh_uses_current_row_when_process_moved_to_later_page(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf"}],
    )
    current_row = _row(proc.nid_proceso, link_id="fresh-link")
    ficha = _ficha_with_docs([Documento("u1", "a.pdf", "", "", "", "", "3")])
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(html="<html>", url="http://x", ficha_id="f1")

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=current_row),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document"),
    ):
        _refresh_watchlist_process(cfg, watch_session, proc)

    mock_client.open_ficha.assert_called_once_with(current_row)


def test_refresh_rejects_empty_ficha_when_existing_content_would_be_wiped(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf"}],
    )
    old_cronograma = proc.cronograma_json
    old_docs = proc.documentos_json
    empty_ficha = FichaData(
        ficha_id="f1",
        nid_proceso=proc.nid_proceso,
        nomenclatura="",
        descripcion="",
        objeto="",
        fecha_publicacion="",
        cronograma=[],
        documentos=[],
    )
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(html="<html>", url="http://x", ficha_id="f1")

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=empty_ficha),
    ):
        with pytest.raises(RuntimeError, match="vacía"):
            _refresh_watchlist_process(cfg, watch_session, proc)

    assert proc.cronograma_json == old_cronograma
    assert proc.documentos_json == old_docs


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
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
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
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document"),
    ):
        refresh_watchlist_processes(cfg, watch_session)

    watch_session.commit()
    watch_session.refresh(proc)
    assert proc.watch_checked_at is not None


def test_refresh_triggers_on_fecha_publicacion_only(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf", "tipo_descarga": "3"}],
    )
    proc.fecha_publicacion = "01/01/26"
    watch_session.flush()
    ficha = _ficha_with_docs([Documento("u1", "a.pdf", "", "", "", "", "3")])
    ficha.fecha_publicacion = "02/01/26"
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://x", ficha_id="f1"
    )

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document"),
    ):
        assert _refresh_watchlist_process(cfg, watch_session, proc) is True

    assert proc.watch_unread is True
    assert proc.fecha_publicacion == "02/01/26"


def test_refresh_fecha_only_preserves_stored_document_names(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    stored_doc = {
        "uuid": "u1",
        "nombre": "Bases-final.pdf",
        "archivo": "Bases-final.pdf",
        "tipo_descarga": "3",
    }
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[stored_doc],
    )
    proc.fecha_publicacion = "01/01/26"
    watch_session.flush()
    ficha = _ficha_with_docs([Documento("u1", "(2646 KB)", "", "", "", "", "3")])
    ficha.fecha_publicacion = "02/01/26"
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://x", ficha_id="f1"
    )

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document"),
    ):
        assert _refresh_watchlist_process(cfg, watch_session, proc) is True

    stored = json.loads(proc.documentos_json or "[]")
    assert stored[0]["archivo"] == "Bases-final.pdf"
    assert stored[0]["nombre"] == "Bases-final.pdf"


def test_refresh_does_not_write_manifest_on_failed_download_validation(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf", "tipo_descarga": "3"}],
    )
    docs_dir = Path(proc.data_dir) / "documentos"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "u1.pdf").write_bytes(b"pdf")
    new_docs = [
        Documento("u1", "a.pdf", "", "", "", "", "3"),
        Documento("u2", "b.pdf", "", "", "", "", "3"),
    ]
    ficha = _ficha_with_docs(new_docs)
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://x", ficha_id="f1"
    )

    def _download(docs_dir, doc, **kwargs):
        path = docs_dir / f"{doc['uuid']}.pdf"
        path.write_bytes(b"" if doc["uuid"] == "u2" else b"pdf")
        doc["archivo"] = path.name
        return doc["uuid"] == "u2"

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document", side_effect=_download),
    ):
        with pytest.raises(RuntimeError):
            _refresh_watchlist_process(cfg, watch_session, proc)

    manifest_path = docs_dir / "manifest.json"
    assert not manifest_path.is_file() or "u2" not in manifest_path.read_text(
        encoding="utf-8"
    )


def test_refresh_persists_post_download_document_names(
    watch_session: Session, tmp_path: Path
):
    cfg = AppConfig()
    proc = _sample_process(
        watch_session,
        tmp_path=tmp_path,
        docs=[{"uuid": "u1", "nombre": "a.pdf", "tipo_descarga": "3"}],
    )
    new_docs = [
        Documento("u1", "a.pdf", "", "", "", "", "3"),
        Documento("u2", "b.pdf", "", "", "", "", "3"),
    ]
    ficha = _ficha_with_docs(new_docs)
    mock_client = MagicMock()
    mock_client.open_ficha.return_value = MagicMock(
        html="<html>", url="http://x", ficha_id="f1"
    )

    def _download(docs_dir, doc, **kwargs):
        doc["archivo"] = f"{doc['uuid']}.pdf"
        doc["nombre"] = f"Canonical-{doc['uuid']}.pdf"
        path = docs_dir / doc["archivo"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pdf")
        return True

    with (
        patch("seace_monitor.watchlist.SeaceClient", return_value=mock_client),
        patch("seace_monitor.watchlist._resolve_current_row", return_value=_row(proc.nid_proceso)),
        patch("seace_monitor.watchlist.parse_ficha", return_value=ficha),
        patch("seace_monitor.watchlist.download_and_store_document", side_effect=_download),
    ):
        assert _refresh_watchlist_process(cfg, watch_session, proc) is True

    stored = json.loads(proc.documentos_json or "[]")
    by_uuid = {item["uuid"]: item for item in stored}
    assert by_uuid["u2"]["nombre"] == "Canonical-u2.pdf"
    assert by_uuid["u2"]["archivo"] == "Canonical-u2.pdf"
