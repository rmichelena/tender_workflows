"""Tests del seam del feed (paso 0.3a del split feed/pipeline)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db.models import Base, Entity, Process, ProcessStatus
from .feed import FeedRepository


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _entity(session, ruc="20123456789"):
    entity = Entity(ruc=ruc, nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    return entity


def test_find_by_ref_matches_source_identity():
    session = _session()
    entity = _entity(session)
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref="100",
        nid_proceso="100",
        nomenclatura="LP-1-2026",
    )
    session.add(proc)
    session.commit()

    repo = FeedRepository(session)
    assert repo.find_by_ref("seace", entity.id, "100") is proc
    # Identidad incluye la fuente: misma ref, otra fuente → no matchea.
    assert repo.find_by_ref("adp_portal", entity.id, "100") is None
    assert repo.find_by_ref("seace", entity.id, "999") is None


def test_query_by_status_filters_status_and_optional_source():
    session = _session()
    entity = _entity(session)
    seace_pub = Process(
        entity_id=entity.id, anio=2026, source="seace", source_ref="1",
        nid_proceso="1", nomenclatura="LP-1", status=ProcessStatus.publicada,
    )
    adp_pub = Process(
        entity_id=entity.id, anio=2026, source="adp_portal", source_ref="A1",
        nid_proceso=None, nomenclatura="ADP-1", status=ProcessStatus.publicada,
    )
    descartada = Process(
        entity_id=entity.id, anio=2026, source="seace", source_ref="2",
        nid_proceso="2", nomenclatura="LP-2", status=ProcessStatus.descartada,
    )
    session.add_all([seace_pub, adp_pub, descartada])
    session.commit()

    repo = FeedRepository(session)
    publicadas = repo.query_by_status([ProcessStatus.publicada]).all()
    assert set(publicadas) == {seace_pub, adp_pub}

    seace_only = repo.query_by_status(
        [ProcessStatus.publicada], source="seace"
    ).all()
    assert seace_only == [seace_pub]
