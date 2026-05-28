"""Tests de fundación multi-ingesta."""

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from .db.models import (
    Base,
    Entity,
    InterestStatus,
    Process,
    ProcessStatus,
    _default_source_ref,
)
from .db.session import (
    _backfill_process_pipeline_fields,
    _backfill_process_sources,
    _ensure_table_columns,
    _migrate_process_identity_schema,
)


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
    assert saved.workflow_profile == "public_tender"
    assert saved.interest_status == InterestStatus.none


def test_source_ref_default_never_returns_none_when_nid_is_absent():
    class FakeContext:
        def get_current_parameters(self):
            return {"source": "email"}

    assert _default_source_ref(FakeContext()) == ""


def test_process_status_includes_autorejected_for_rule_filters():
    assert ProcessStatus.autorejected.value == "autorejected"


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


def test_backfills_pipeline_fields_for_existing_process_rows(tmp_path: Path):
    db_path = tmp_path / "legacy_pipeline.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE processes (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO processes (id) VALUES (1)"))

    _ensure_table_columns(
        engine,
        "processes",
        (
            ("workflow_profile", "VARCHAR(64) DEFAULT 'public_tender'"),
            ("interest_status", "VARCHAR(32) DEFAULT 'none'"),
        ),
    )
    _backfill_process_pipeline_fields(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT workflow_profile, interest_status FROM processes WHERE id = 1")
        ).one()
        assert row.workflow_profile == "public_tender"
        assert row.interest_status == "none"


def test_seace_ingest_adapter_is_registered():
    from .ingest import get_adapter

    adapter = get_adapter("seace")
    assert adapter.source == "seace"
    assert adapter.capabilities.scan_listings is True
    assert adapter.capabilities.fetch_by_reference is True


def test_non_seace_process_persists_without_nid_proceso():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    entity = Entity(ruc="20999999999", nombre="Cliente privado", activa=True)
    session.add(entity)
    session.flush()

    message_id = "very-long-message-id@mail.example.com"
    proc = Process(
        entity_id=entity.id,
        anio=2026,
        source="email",
        source_ref=message_id,
        nomenclatura="Estudio mercado Q2",
        nid_proceso=None,
    )
    session.add(proc)
    session.commit()

    saved = session.query(Process).one()
    assert saved.source == "email"
    assert saved.source_ref == message_id
    assert saved.nid_proceso is None


def test_process_source_identity_unique_per_entity(tmp_path: Path):
    db_path = tmp_path / "identity.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    entity = Entity(ruc="20111111111", nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    session.add(
        Process(
            entity_id=entity.id,
            anio=2026,
            source="email",
            source_ref="ref-1",
            nomenclatura="A",
            nid_proceso=None,
        )
    )
    session.commit()
    session.add(
        Process(
            entity_id=entity.id,
            anio=2026,
            source="email",
            source_ref="ref-1",
            nomenclatura="B",
            nid_proceso=None,
        )
    )
    try:
        session.commit()
        raised = False
    except Exception:
        session.rollback()
        raised = True
    assert raised


def test_migrate_process_identity_schema_relaxes_legacy_sqlite_nid(tmp_path: Path):
    db_path = tmp_path / "legacy_identity.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE entities (id INTEGER PRIMARY KEY, ruc VARCHAR(11), "
                "nombre VARCHAR(512), activa BOOLEAN DEFAULT 1)"
            )
        )
        conn.execute(text("INSERT INTO entities (id, ruc, nombre) VALUES (1, '20123456789', 'E')"))
        conn.execute(
            text(
                "CREATE TABLE processes ("
                "id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL, anio INTEGER NOT NULL, "
                "nid_proceso VARCHAR(32) NOT NULL, nomenclatura VARCHAR(256) NOT NULL, "
                "UNIQUE(entity_id, nid_proceso))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO processes (id, entity_id, anio, nid_proceso, nomenclatura) "
                "VALUES (1, 1, 2026, '987654', 'AS-SM-1-2026')"
            )
        )

    _ensure_table_columns(
        engine,
        "processes",
        (("source", "VARCHAR(32) DEFAULT 'seace'"), ("source_ref", "VARCHAR(256)")),
    )
    _migrate_process_identity_schema(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT source, source_ref, nid_proceso FROM processes WHERE id = 1")
        ).one()
        assert row.source == "seace"
        assert row.source_ref == "987654"
        assert row.nid_proceso == "987654"
        insp = inspect(engine)
        assert any(
            set(uc.get("column_names") or ()) == {"source", "entity_id", "source_ref"}
            for uc in insp.get_unique_constraints("processes")
        )
