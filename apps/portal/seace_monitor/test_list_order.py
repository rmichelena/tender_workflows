"""Tests para correlativos en listas descargados / analizados."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .db.models import Base, Entity, Process, ProcessStatus
from .list_order import (
    enter_analizados_list,
    enter_descargados_list,
    leave_analizados_list,
    leave_descargados_list,
)


@pytest.fixture
def session(tmp_path: Path) -> tuple[Session, Entity]:
    engine = create_engine(f"sqlite:///{tmp_path / 'list_order.db'}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    entity = Entity(ruc="20100000001", nombre="Entidad test", activa=True)
    db.add(entity)
    db.flush()
    return db, entity


def _proc(
    session: Session,
    entity: Entity,
    *,
    nid: str,
    status: ProcessStatus,
) -> Process:
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        nid_proceso=nid,
        nomenclatura=f"N-{nid}",
        status=status,
    )
    session.add(proc)
    session.flush()
    return proc


def test_enter_descargados_appends_sequential(session: tuple[Session, Entity]):
    db, entity = session
    p1 = _proc(db, entity, nid="1", status=ProcessStatus.descargada)
    p2 = _proc(db, entity, nid="2", status=ProcessStatus.descargada)

    enter_descargados_list(db, p1)
    enter_descargados_list(db, p2)

    assert p1.list_rank_descargados == 1
    assert p2.list_rank_descargados == 2


def test_leave_descargados_renumbers(session: tuple[Session, Entity]):
    db, entity = session
    p1 = _proc(db, entity, nid="1", status=ProcessStatus.descargada)
    p2 = _proc(db, entity, nid="2", status=ProcessStatus.descargada)
    p3 = _proc(db, entity, nid="3", status=ProcessStatus.descargada)
    p1.list_rank_descargados = 1
    p2.list_rank_descargados = 2
    p3.list_rank_descargados = 3
    db.flush()

    leave_descargados_list(db, p2)

    assert p2.list_rank_descargados is None
    db.refresh(p1)
    db.refresh(p3)
    assert p1.list_rank_descargados == 1
    assert p3.list_rank_descargados == 2


def test_enter_analizados_appends_at_end(session: tuple[Session, Entity]):
    db, entity = session
    p1 = _proc(db, entity, nid="1", status=ProcessStatus.analizada)
    p2 = _proc(db, entity, nid="2", status=ProcessStatus.analizada)
    p1.list_rank_analizados = 1
    db.flush()

    enter_analizados_list(db, p2)

    assert p2.list_rank_analizados == 2


def test_restore_archived_goes_to_end(session: tuple[Session, Entity]):
    db, entity = session
    kept = _proc(db, entity, nid="1", status=ProcessStatus.analizada)
    restored = _proc(db, entity, nid="2", status=ProcessStatus.archivada)
    kept.list_rank_analizados = 1
    db.flush()

    restored.status = ProcessStatus.analizada
    enter_analizados_list(db, restored)

    assert kept.list_rank_analizados == 1
    assert restored.list_rank_analizados == 2


def test_leave_analizados_renumbers(session: tuple[Session, Entity]):
    db, entity = session
    p1 = _proc(db, entity, nid="1", status=ProcessStatus.analizada)
    p2 = _proc(db, entity, nid="2", status=ProcessStatus.portafolio)
    p1.list_rank_analizados = 1
    p2.list_rank_analizados = 2
    db.flush()

    leave_analizados_list(db, p1)

    assert p1.list_rank_analizados is None
    db.refresh(p2)
    assert p2.list_rank_analizados == 1
