"""Reconciliación de re-publicaciones SEACE por nomenclatura (UID de negocio).

Cuando SEACE re-publica un proceso interrumpido conserva la nomenclatura pero reasigna
el nid; sin esto el scanner crearía un duplicado `publicada` junto al item ya
descargado/analizado. Ver scanner.adopt_republication.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .client import ProcessRow
from .db.models import Base, Entity, Process, ProcessStatus
from .feed import FeedRepository
from .scanner import (
    _REPUBLICATION_CLAIMED_STATUSES,
    adopt_republication,
    is_removable_publicada_duplicate,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _entity(session):
    entity = Entity(ruc="20123456789", nombre="CORPAC", activa=True)
    session.add(entity)
    session.flush()
    return entity


def _row(nid: str, nomenclatura: str, reiniciado_desde: str = "") -> ProcessRow:
    return ProcessRow(
        row_index=0,
        numero="1",
        fecha_publicacion="01/06/2026",
        nomenclatura=nomenclatura,
        reiniciado_desde=reiniciado_desde,
        objeto="Servicio",
        descripcion="x",
        cuantia="1",
        moneda="PEN",
        version_seace="1",
        nid_proceso=nid,
        nid_convocatoria="c",
        nid_sistema="s",
        link_id="l",
        ntipo="t",
    )


def _proc(entity, *, source_ref, nid, nomenclatura, status, data_dir=None):
    return Process(
        entity_id=entity.id,
        anio=2026,
        source="seace",
        source_ref=source_ref,
        nid_proceso=nid,
        nomenclatura=nomenclatura,
        status=status,
        data_dir=data_dir,
    )


NOM = "LP-ABR-1-2026-CORPAC S.A.-1"


def test_adopt_deletes_publicada_duplicate_and_adopts_nid():
    session = _session()
    entity = _entity(session)
    claimed = _proc(
        entity, source_ref="1001133", nid="1018219", nomenclatura=NOM,
        status=ProcessStatus.analizada, data_dir="/data/x",
    )
    dup = _proc(
        entity, source_ref="1018219", nid="1018219", nomenclatura=NOM,
        status=ProcessStatus.publicada,
    )
    session.add_all([claimed, dup])
    session.commit()
    dup_id = dup.id

    adopt_republication(
        session, claimed, dup, _row("1018219", NOM, reiniciado_desde="Registro")
    )
    session.commit()

    assert session.get(Process, dup_id) is None  # duplicado eliminado
    assert claimed.source_ref == "1018219"  # identidad adoptada
    assert claimed.nid_proceso == "1018219"
    assert claimed.status == ProcessStatus.analizada  # se conserva el item trabajado
    assert claimed.reiniciado_desde == "Registro"
    assert claimed.list_hash  # sincronizado con la fila actual


def test_adopt_with_no_existing_row_adopts_nid():
    session = _session()
    entity = _entity(session)
    claimed = _proc(
        entity, source_ref="1001133", nid="1001133", nomenclatura=NOM,
        status=ProcessStatus.descargada,
    )
    session.add(claimed)
    session.commit()

    adopt_republication(session, claimed, None, _row("1018219", NOM))
    session.commit()

    assert claimed.source_ref == "1018219"
    assert claimed.nid_proceso == "1018219"


def test_adopt_keeps_nonremovable_existing_without_changing_identity():
    session = _session()
    entity = _entity(session)
    claimed = _proc(
        entity, source_ref="1001133", nid="1001133", nomenclatura=NOM,
        status=ProcessStatus.analizada,
    )
    # "existing" no es un duplicado borrable (tiene data_dir) → no se toca la identidad.
    existing = _proc(
        entity, source_ref="1018219", nid="1018219", nomenclatura=NOM,
        status=ProcessStatus.publicada, data_dir="/data/y",
    )
    session.add_all([claimed, existing])
    session.commit()
    existing_id = existing.id

    adopt_republication(session, claimed, existing, _row("1018219", NOM))
    session.commit()

    assert session.get(Process, existing_id) is not None  # no se borra
    assert claimed.source_ref == "1001133"  # identidad sin cambios (evita colisión)


def test_is_removable_publicada_duplicate_guards():
    session = _session()
    entity = _entity(session)
    pub = _proc(entity, source_ref="1", nid="1", nomenclatura=NOM,
                status=ProcessStatus.publicada)
    pub_with_dir = _proc(entity, source_ref="2", nid="2", nomenclatura=NOM,
                         status=ProcessStatus.publicada, data_dir="/d")
    analizada = _proc(entity, source_ref="3", nid="3", nomenclatura=NOM,
                      status=ProcessStatus.analizada)
    session.add_all([pub, pub_with_dir, analizada])
    session.commit()

    assert is_removable_publicada_duplicate(pub) is True
    assert is_removable_publicada_duplicate(pub_with_dir) is False
    assert is_removable_publicada_duplicate(analizada) is False


def test_claimed_for_entity_only_returns_claimed_statuses():
    session = _session()
    entity = _entity(session)
    a = _proc(entity, source_ref="1", nid="1", nomenclatura="A",
              status=ProcessStatus.analizada)
    d = _proc(entity, source_ref="2", nid="2", nomenclatura="B",
              status=ProcessStatus.descargada)
    p = _proc(entity, source_ref="3", nid="3", nomenclatura="C",
              status=ProcessStatus.publicada)
    x = _proc(entity, source_ref="4", nid="4", nomenclatura="D",
              status=ProcessStatus.descartada)
    session.add_all([a, d, p, x])
    session.commit()

    claimed = FeedRepository(session).claimed_for_entity(
        "seace", entity.id, _REPUBLICATION_CLAIMED_STATUSES
    )
    assert {c.nomenclatura for c in claimed} == {"A", "B"}
