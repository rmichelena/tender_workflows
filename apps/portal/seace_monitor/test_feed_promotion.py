"""Tests de la promoción feed→pipeline (paso 0.3d-1)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .db.models import (
    AnalysisResult,
    Base,
    Entity,
    InterestStatus,
    Process,
    ProcessStatus,
)
from .db.session import _backfill_promoted_at
from .feed import is_promoted, promote, should_be_promoted


def _setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20123456789", nombre="E", activa=True)
    session.add(entity)
    session.flush()
    return engine, session, entity


def _proc(entity, *, ref, status=ProcessStatus.publicada, **kwargs):
    return Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref=ref,
        nid_proceso=ref,
        nomenclatura=f"NOM-{ref}",
        status=status,
        **kwargs,
    )


def test_promote_sets_timestamp_once_and_is_idempotent():
    _, session, entity = _setup()
    proc = _proc(entity, ref="1")
    session.add(proc)
    session.flush()

    assert is_promoted(proc) is False
    assert promote(session, proc) is True
    first = proc.promoted_at
    assert first is not None
    assert is_promoted(proc) is True
    # Segundo llamado no re-escribe (latch).
    assert promote(session, proc) is False
    assert proc.promoted_at == first


def test_should_be_promoted_predicate():
    _, session, entity = _setup()
    feed_pure = _proc(entity, ref="pub", status=ProcessStatus.publicada)
    autorejected = _proc(entity, ref="ar", status=ProcessStatus.autorejected)
    by_status = _proc(entity, ref="dl", status=ProcessStatus.descargada)
    by_interest = _proc(
        entity, ref="int", status=ProcessStatus.publicada,
        interest_status=InterestStatus.watching,
    )
    by_data_dir = _proc(
        entity, ref="dd", status=ProcessStatus.publicada, data_dir="/x"
    )
    session.add_all([feed_pure, autorejected, by_status, by_interest, by_data_dir])
    session.flush()

    assert should_be_promoted(feed_pure) is False
    assert should_be_promoted(autorejected) is False
    assert should_be_promoted(by_status) is True
    assert should_be_promoted(by_interest) is True
    assert should_be_promoted(by_data_dir) is True


def test_should_be_promoted_with_analysis():
    _, session, entity = _setup()
    proc = _proc(entity, ref="an", status=ProcessStatus.publicada)
    session.add(proc)
    session.flush()
    session.add(AnalysisResult(process_id=proc.id, status="done"))
    session.flush()
    session.refresh(proc)
    assert should_be_promoted(proc) is True


def test_backfill_promoted_at_marks_only_curated_and_is_idempotent():
    engine, session, entity = _setup()
    feed_pure = _proc(entity, ref="pub", status=ProcessStatus.publicada)
    autorejected = _proc(entity, ref="ar", status=ProcessStatus.autorejected)
    descargada = _proc(entity, ref="dl", status=ProcessStatus.descargada)
    interes = _proc(
        entity, ref="int", status=ProcessStatus.publicada,
        interest_status=InterestStatus.candidate,
    )
    con_data = _proc(
        entity, ref="dd", status=ProcessStatus.publicada, data_dir="/x"
    )
    session.add_all([feed_pure, autorejected, descargada, interes, con_data])
    session.commit()

    n = _backfill_promoted_at(engine)
    session.expire_all()
    assert n == 3
    assert session.get(Process, feed_pure.id).promoted_at is None
    assert session.get(Process, autorejected.id).promoted_at is None
    assert session.get(Process, descargada.id).promoted_at is not None
    assert session.get(Process, interes.id).promoted_at is not None
    assert session.get(Process, con_data.id).promoted_at is not None

    # Idempotente: segunda corrida no toca nada.
    assert _backfill_promoted_at(engine) == 0


def test_backfill_does_not_overwrite_existing_promoted_at():
    engine, session, entity = _setup()
    proc = _proc(entity, ref="dl", status=ProcessStatus.descargada)
    fixed = datetime(2020, 1, 1, tzinfo=timezone.utc)
    proc.promoted_at = fixed
    session.add(proc)
    session.commit()

    assert _backfill_promoted_at(engine) == 0
    session.expire_all()
    # SQLite guarda el datetime sin tzinfo; comparamos el valor naive.
    assert session.get(Process, proc.id).promoted_at == fixed.replace(tzinfo=None)


def test_backfill_marks_process_with_analysis():
    engine, session, entity = _setup()
    proc = _proc(entity, ref="an", status=ProcessStatus.publicada)
    session.add(proc)
    session.flush()
    session.add(AnalysisResult(process_id=proc.id, status="done"))
    session.commit()

    assert _backfill_promoted_at(engine) == 1
    session.expire_all()
    assert session.get(Process, proc.id).promoted_at is not None


# --- 0.3d-2: promoción en las acciones positivas -----------------------------------


def test_begin_download_transition_promotes(tmp_path):
    from .config import AppConfig
    from .db.session import init_db, session_factory
    from .web.workflow_transitions import begin_download_transition

    init_db(f"sqlite:///{tmp_path / 'dl.db'}")
    db = session_factory()
    try:
        entity = Entity(ruc="20123456789", nombre="E", activa=True)
        db.add(entity)
        db.flush()
        proc = _proc(entity, ref="1", status=ProcessStatus.publicada)
        db.add(proc)
        db.commit()
        pid = proc.id

        begin_download_transition(db, proc)
        reloaded = db.get(Process, pid)
        assert reloaded.status == ProcessStatus.descargando
        assert reloaded.promoted_at is not None
    finally:
        db.close()


def test_marking_interest_promotes(tmp_path):
    from fastapi.testclient import TestClient

    from .config import AppConfig
    from .db.session import session_factory
    from .web.app import create_app

    cfg = AppConfig(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'int.db'}")
    app = create_app(cfg)
    db = session_factory()
    try:
        entity = Entity(ruc="20123456789", nombre="E", activa=True)
        db.add(entity)
        db.flush()
        proc = _proc(entity, ref="1", status=ProcessStatus.publicada)
        db.add(proc)
        db.commit()
        pid = proc.id
    finally:
        db.close()

    resp = TestClient(app).post(
        f"/processes/{pid}/interest",
        data={"interest_status": "candidate", "return_to": "/analizados"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    db = session_factory()
    try:
        reloaded = db.get(Process, pid)
        assert reloaded.interest_status == InterestStatus.candidate
        assert reloaded.promoted_at is not None
    finally:
        db.close()
