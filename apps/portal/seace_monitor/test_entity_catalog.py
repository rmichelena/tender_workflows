"""Tests catálogo OSCE y filtros de escaneo."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .db.models import Base, Entity
from .entity_catalog import _decode_catalog_bytes, parse_official_entities, sync_official_entities
from .scan_options import (
    ScanDateMode,
    default_since_date,
    parse_ddmmyy,
    parse_seace_date,
    passes_date_filter,
)

LIMA = ZoneInfo("America/Lima")
SAMPLE = """RUC|NOMBRE_DE_ENTIDAD|DEPARTAMENTO|PROVINCIA|DISTRITO|CODIGO_SIAF|CODCONSUCODE|ESTADO|ULTIMAACTUALIZACION
20290898685|ACADEMIA DE LA MAGISTRATURA|LIMA|LIMA|JESUS MARIA|0014|1940|Activo|17/07/13
99999999999|ENTIDAD INACTIVA|LIMA|LIMA|LIMA|0001|0001|Inactivo|01/01/20
11111111111|ENTIDAD NUEVA ACTIVA|LIMA|LIMA|LIMA|0002|0002|Activo|01/01/24
"""


def test_decode_catalog_bytes_cp1252_peru():
    raw = (
        b"20122476309|BANCO CENTRAL DE RESERVA DEL PER\xda|LIMA|LIMA|LIMA||1926|Activo|17/07/13\r\n"
    )
    text = _decode_catalog_bytes(raw)
    assert "PERÚ" in text or "PER\u00da" in text
    rows = parse_official_entities("RUC|NOMBRE_DE_ENTIDAD|DEPARTAMENTO|PROVINCIA|DISTRITO|CODIGO_SIAF|CODCONSUCODE|ESTADO|ULTIMAACTUALIZACION\n" + text)
    assert rows[0].nombre.endswith("PERÚ")


def test_parse_official_entities_pipe():
    rows = parse_official_entities(SAMPLE)
    assert len(rows) == 3
    assert rows[0].ruc == "20290898685"
    assert rows[1].estado == "Inactivo"


def test_sync_skips_new_inactivo():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    rows = parse_official_entities(SAMPLE)
    result = sync_official_entities(session, rows)
    session.commit()
    assert result.added == 2
    assert result.skipped_inactivo == 1
    inactivo = session.query(Entity).filter(Entity.ruc == "99999999999").one_or_none()
    assert inactivo is None


def test_sync_updates_existing_inactivo_without_delete():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(
        Entity(
            ruc="99999999999",
            nombre="Vieja",
            activa=True,
            estado_osce="Activo",
        )
    )
    session.commit()
    rows = parse_official_entities(SAMPLE)
    sync_official_entities(session, rows)
    session.commit()
    ent = session.query(Entity).filter(Entity.ruc == "99999999999").one()
    assert ent.estado_osce == "Inactivo"
    assert ent.activa is True


def test_passes_date_filter_future():
    now = datetime(2026, 5, 19, 12, 0, tzinfo=LIMA)
    assert passes_date_filter(
        "27/05/2026 15:00",
        mode=ScanDateMode.future_presentacion,
        now=now,
    )
    assert not passes_date_filter(
        "01/05/2026 15:00",
        mode=ScanDateMode.future_presentacion,
        now=now,
    )


def test_parse_ddmmyy_and_default_since():
    assert parse_ddmmyy("19/05/25") == date(2025, 5, 19)
    d = default_since_date(datetime(2026, 5, 19, tzinfo=LIMA))
    assert d == date(2025, 5, 19)


def test_parse_seace_date_formats():
    assert parse_seace_date("17/07/2013") is not None
    assert parse_seace_date("27/05/2026 15:00") is not None
    assert parse_seace_date("26/01/50") is None


def test_passes_date_filter_since_date_falls_back_to_publicacion():
    since = date(2026, 1, 1)
    assert passes_date_filter(
        None,
        fecha_publicacion="15/03/2026",
        mode=ScanDateMode.since_date,
        since_date=since,
    )
    assert not passes_date_filter(
        None,
        fecha_publicacion="15/12/2025",
        mode=ScanDateMode.since_date,
        since_date=since,
    )
