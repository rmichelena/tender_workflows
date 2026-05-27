"""Tests de fundación multi-ingesta."""

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .db.models import Base, Entity, Process
from .db.session import _backfill_process_sources, _ensure_table_columns


def test_process_defaults_to_seace_source_ref_from_nid():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    entity = Entity(ruc="20123456789", nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()

    proc = Process(
        entity_id=entity.id,
        anio=2026,
        nid_proceso="123456",
        nomenclatura="AS-SM-1-2026",
    )
    session.add(proc)
    session.commit()

    saved = session.query(Process).one()
    assert saved.source == "seace"
    assert saved.source_ref == "123456"


def test_backfills_source_columns_for_existing_process_rows(tmp_path: Path):
    db_path = tmp_path / "legacy.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE processes (id INTEGER PRIMARY KEY, nid_proceso VARCHAR(32))"))
        conn.execute(text("INSERT INTO processes (id, nid_proceso) VALUES (1, '987654')"))

    _ensure_table_columns(
        engine,
        "processes",
        (("source", "VARCHAR(32) DEFAULT 'seace'"), ("source_ref", "VARCHAR(256)")),
    )
    _backfill_process_sources(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT source, source_ref FROM processes WHERE id = 1")
        ).one()
        assert row.source == "seace"
        assert row.source_ref == "987654"


def test_seace_ingest_adapter_is_registered():
    from .ingest import get_adapter

    adapter = get_adapter("seace")
    assert adapter.source == "seace"
    assert adapter.capabilities.scan_listings is True
    assert adapter.capabilities.fetch_by_reference is True
