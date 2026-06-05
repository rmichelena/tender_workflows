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
    build_claimed_nomenclatura_map,
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


def test_adopt_does_not_regress_to_older_nid():
    # Hallazgo review #1: dos filas misma nomenclatura en el mismo scan; la fila con nid
    # más viejo NO debe revertir la identidad ya avanzada.
    session = _session()
    entity = _entity(session)
    claimed = _proc(
        entity, source_ref="1018219", nid="1018219", nomenclatura=NOM,
        status=ProcessStatus.analizada,
    )
    session.add(claimed)
    session.commit()

    # Llega una fila con el nid viejo (1001133 < 1018219): no debe adoptar.
    adopt_republication(session, claimed, None, _row("1001133", NOM))
    session.commit()

    assert claimed.source_ref == "1018219"
    assert claimed.nid_proceso == "1018219"


def test_build_claimed_map_prefers_more_advanced_on_collision():
    # Hallazgo review #2: dos reclamados con misma nomenclatura → elegir el más avanzado
    # (y a igualdad, nid mayor), sin depender del orden de la consulta.
    session = _session()
    entity = _entity(session)
    descargada = _proc(entity, source_ref="100", nid="100", nomenclatura=NOM,
                       status=ProcessStatus.descargada)
    analizada = _proc(entity, source_ref="50", nid="50", nomenclatura=NOM,
                      status=ProcessStatus.analizada)
    session.add_all([descargada, analizada])
    session.commit()

    # Orden de entrada irrelevante: gana 'analizada' (más avanzada) aunque tenga nid menor.
    for order in ([descargada, analizada], [analizada, descargada]):
        mapping = build_claimed_nomenclatura_map(order)
        assert mapping[NOM] is analizada


def test_build_claimed_map_breaks_status_tie_by_newer_nid():
    session = _session()
    entity = _entity(session)
    old = _proc(entity, source_ref="100", nid="100", nomenclatura=NOM,
                status=ProcessStatus.descargada)
    new = _proc(entity, source_ref="200", nid="200", nomenclatura=NOM,
                status=ProcessStatus.descargada)
    session.add_all([old, new])
    session.commit()

    assert build_claimed_nomenclatura_map([old, new])[NOM] is new
    assert build_claimed_nomenclatura_map([new, old])[NOM] is new


def test_publicada_map_picks_newest_nid_per_nomenclatura():
    # (5) Dedupe del feed puro: dos `publicada` con misma nomenclatura → el mapa elige la
    # de nid más reciente, para fusionar la otra en ella.
    from .config import AppConfig
    from .scanner import MultiEntityScanner

    session = _session()
    entity = _entity(session)
    old = _proc(entity, source_ref="100", nid="100", nomenclatura=NOM,
                status=ProcessStatus.publicada)
    new = _proc(entity, source_ref="200", nid="200", nomenclatura=NOM,
                status=ProcessStatus.publicada)
    other = _proc(entity, source_ref="9", nid="9", nomenclatura="OTRA",
                  status=ProcessStatus.publicada)
    session.add_all([old, new, other])
    session.commit()

    scanner = MultiEntityScanner(AppConfig(), session)
    mapping = scanner._publicada_nomenclatura_map(entity)

    from .seace_search import normalize_nomenclatura

    assert mapping[normalize_nomenclatura(NOM)] is new
    assert mapping[normalize_nomenclatura("OTRA")] is other


def test_adopt_merges_two_publicada_deleting_older_duplicate():
    # Caso legacy: existen dos `publicada` (nid viejo y nuevo). Al escanear la fila vieja,
    # se elimina el duplicado viejo conservando la `publicada` con nid más reciente.
    session = _session()
    entity = _entity(session)
    new = _proc(entity, source_ref="200", nid="200", nomenclatura=NOM,
                status=ProcessStatus.publicada)
    old = _proc(entity, source_ref="100", nid="100", nomenclatura=NOM,
                status=ProcessStatus.publicada)
    session.add_all([new, old])
    session.commit()
    old_id = old.id

    # `pub` (preferida) = new; `proc` hallado por nid viejo = old (duplicado borrable).
    adopt_republication(session, new, old, _row("100", NOM))
    session.commit()

    assert session.get(Process, old_id) is None  # duplicado viejo eliminado
    assert new.source_ref == "200"  # no regresa a nid viejo


def test_adopt_fresh_publicada_republication_adopts_new_nid():
    # Re-publicación fresca: solo existe la `publicada` vieja; aparece el nid nuevo (no en
    # BD). Se adopta el nid nuevo en la fila existente, sin crear un segundo `publicada`.
    session = _session()
    entity = _entity(session)
    pub = _proc(entity, source_ref="100", nid="100", nomenclatura=NOM,
                status=ProcessStatus.publicada)
    session.add(pub)
    session.commit()

    adopt_republication(session, pub, None, _row("200", NOM))
    session.commit()

    assert pub.source_ref == "200"
    assert pub.nid_proceso == "200"


def test_scan_dedupes_publicada_within_single_scan(monkeypatch):
    # Review #1 (este turno): el mapa de dedupe debe mantenerse al día DENTRO del mismo
    # escaneo. Dos filas con la misma nomenclatura y nids distintos en un solo ciclo deben
    # producir una sola `publicada` (adoptando el nid más reciente), no dos paralelas.
    from .client import FichaResult
    from .config import AppConfig
    from .parser import FichaData
    from .scan_options import ScanOptions
    from .scanner import MultiEntityScanner

    session = _session()
    entity = _entity(session)
    session.commit()

    rows = [_row("100", NOM), _row("200", NOM)]

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def fetch_list_page(self, page):
            return (None, page)

        def parse_rows(self, soup):
            return rows if soup == 0 else []

        def total_pages(self, soup):
            return 1

        def open_ficha(self, row):
            return FichaResult(
                ficha_id=f"f{row.nid_proceso}", html="<html>", url="http://x"
            )

        def refresh_list_page_state(self, page):
            return None

    monkeypatch.setattr("seace_monitor.scanner.SeaceClient", _FakeClient)
    monkeypatch.setattr(
        "seace_monitor.scanner.parse_ficha",
        lambda html, ficha_id, nid: FichaData(
            ficha_id=ficha_id,
            nid_proceso=nid,
            nomenclatura=NOM,
            descripcion="x",
            objeto="Servicio",
            fecha_publicacion="01/06/2026",
        ),
    )

    scanner = MultiEntityScanner(AppConfig(), session)
    scanner._scan_entity(entity, ScanOptions())
    session.commit()

    procs = session.query(Process).filter(Process.nomenclatura == NOM).all()
    assert len(procs) == 1  # una sola publicada pese a dos nids en el mismo scan
    assert procs[0].source_ref == "200"  # adopta el nid más reciente


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
