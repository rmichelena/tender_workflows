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


def test_process_defaults_to_licitacion_lifecycle_phase():
    from .db.models import LifecyclePhase

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    entity = Entity(ruc="20123456789", nombre="Entidad", activa=True)
    session.add(entity)
    session.flush()
    proc = Process(
        entity_id=entity.id, anio=2026, nid_proceso="1", nomenclatura="AS-SM-1-2026"
    )
    session.add(proc)
    session.commit()

    assert session.query(Process).one().lifecycle_phase == LifecyclePhase.licitacion


def test_backfills_lifecycle_phase_for_existing_process_rows(tmp_path: Path):
    db_path = tmp_path / "legacy_lifecycle.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE processes ("
                "id INTEGER PRIMARY KEY, workflow_profile VARCHAR(64), "
                "interest_status VARCHAR(32))"
            )
        )
        conn.execute(text("INSERT INTO processes (id) VALUES (1)"))

    _ensure_table_columns(
        engine,
        "processes",
        (
            ("workflow_profile", "VARCHAR(64) DEFAULT 'public_tender'"),
            ("interest_status", "VARCHAR(32) DEFAULT 'none'"),
            ("lifecycle_phase", "VARCHAR(32) DEFAULT 'licitacion'"),
        ),
    )
    _backfill_process_pipeline_fields(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT lifecycle_phase FROM processes WHERE id = 1")
        ).one()
        assert row.lifecycle_phase == "licitacion"


def test_seace_ingest_adapter_is_registered():
    from .ingest import get_adapter

    adapter = get_adapter("seace")
    assert adapter.source == "seace"
    assert adapter.capabilities.scan_listings is True
    assert adapter.capabilities.fetch_by_reference is True


def test_adp_ingest_adapter_is_registered_and_external():
    from .ingest import get_adapter, registered_sources

    assert {"seace", "adp_portal"}.issubset(set(registered_sources()))
    adapter = get_adapter("adp_portal")
    assert adapter.source == "adp_portal"
    assert adapter.view_label == "ADP"
    assert adapter.capabilities.opens_external_portal is True
    assert adapter.portal_url


def test_get_adapter_is_case_insensitive_and_raises_for_unknown():
    from .ingest import get_adapter
    from .ingest.base import UnknownIngestSource

    assert get_adapter("SEACE").source == "seace"
    try:
        get_adapter("telepatia")
        raised = False
    except UnknownIngestSource:
        raised = True
    assert raised


def test_ui_helpers_delegate_to_registry_without_source_branching():
    """can_open/label/url salen del adapter, no de condicionales por source."""
    from types import SimpleNamespace

    from .web.seace_view import (
        can_open_seace,
        can_open_source,
        source_button_label,
        source_view_url,
    )

    adp = SimpleNamespace(
        source="adp_portal",
        source_ref="LPN-003-2026-ADP",
        nomenclatura="LPN-003-2026-ADP",
        entity=object(),
        id=42,
    )
    assert can_open_source(adp) is True
    assert can_open_seace(adp) is False  # ADP no usa el proxy SEACE
    assert source_button_label(adp) == "Ver en ADP"
    assert source_view_url(adp) == "https://www.adp.com.pe/"

    seace = SimpleNamespace(
        source="seace",
        source_ref="123456",
        nomenclatura="AS-SM-1-2026",
        entity=object(),
        id=7,
    )
    assert can_open_source(seace) is True
    assert can_open_seace(seace) is True
    assert source_button_label(seace) == "Ver en SEACE"
    assert source_view_url(seace) == "/seace/open/7"

    unknown = SimpleNamespace(
        source="email", source_ref="x", nomenclatura="x", entity=object(), id=1
    )
    assert can_open_source(unknown) is False
    assert can_open_seace(unknown) is False  # fuente sin registrar no pasa el gate del proxy


def test_can_open_seace_is_case_insensitive_like_registry():
    """`can_open_seace` usa el source canónico del adapter, no `process.source` crudo."""
    from types import SimpleNamespace

    from .web.seace_view import can_open_seace, can_open_source

    upper = SimpleNamespace(
        source="SEACE",
        source_ref="123456",
        nomenclatura="AS-SM-1-2026",
        entity=object(),
        id=9,
    )
    assert can_open_source(upper) is True
    assert can_open_seace(upper) is True  # coherente con get_adapter("SEACE")


def test_can_open_negative_cases_for_registered_sources():
    from types import SimpleNamespace

    from .web.seace_view import can_open_seace, can_open_source

    seace_sin_nomenclatura = SimpleNamespace(
        source="seace", source_ref="1", nomenclatura="", entity=object(), id=2
    )
    assert can_open_source(seace_sin_nomenclatura) is False
    assert can_open_seace(seace_sin_nomenclatura) is False

    seace_sin_entity = SimpleNamespace(
        source="seace", source_ref="1", nomenclatura="AS-SM-1-2026", entity=None, id=3
    )
    assert can_open_source(seace_sin_entity) is False

    adp_sin_ref = SimpleNamespace(
        source="adp_portal", source_ref="", nomenclatura="x", entity=object(), id=4
    )
    assert can_open_source(adp_sin_ref) is False


def _fake_worker_config(adp_enabled: bool = True):
    from types import SimpleNamespace

    return SimpleNamespace(
        poll_interval_seconds=321,
        watchlist_refresh_seconds=654,
        adp=SimpleNamespace(enabled=adp_enabled, poll_interval_seconds=987),
    )


def test_adapter_scan_contract_reads_config():
    from .ingest import get_adapter

    cfg = _fake_worker_config(adp_enabled=True)

    seace = get_adapter("seace")
    assert seace.scan_enabled(cfg) is True
    assert seace.scan_interval_seconds(cfg) == 321
    assert seace.watch_interval_seconds(cfg) == 654

    adp = get_adapter("adp_portal")
    assert adp.scan_enabled(cfg) is True
    assert adp.scan_interval_seconds(cfg) == 987
    assert adp.watch_interval_seconds(cfg) == 654

    cfg.adp.enabled = False
    assert adp.scan_enabled(cfg) is False


def test_active_scan_adapters_orders_by_priority_and_respects_adp_flag():
    from .worker import _active_scan_adapters

    on = [a.source for a in _active_scan_adapters(_fake_worker_config(True))]
    assert on == ["seace", "adp_portal"]  # SEACE primero por scan_priority

    off = [a.source for a in _active_scan_adapters(_fake_worker_config(False))]
    assert off == ["seace"]  # ADP deshabilitado queda fuera del worker


def test_seace_adapter_scan_and_watch_delegate(monkeypatch):
    from . import scanner as scanner_mod
    from . import watchlist as watchlist_mod
    from .ingest import get_adapter

    calls: dict[str, object] = {}

    class FakeScanner:
        def __init__(self, cfg, session):
            calls["scan_init"] = (cfg, session)

        def run_once(self):
            return 5

    def fake_watch(cfg, session):
        calls["watch"] = (cfg, session)
        return 3

    monkeypatch.setattr(scanner_mod, "MultiEntityScanner", FakeScanner)
    monkeypatch.setattr(watchlist_mod, "refresh_watchlist_processes", fake_watch)

    adapter = get_adapter("seace")
    assert adapter.scan("CFG", "SES") == 5
    assert calls["scan_init"] == ("CFG", "SES")
    assert adapter.refresh_watchlist("CFG", "SES") == 3
    assert calls["watch"] == ("CFG", "SES")


def test_adp_adapter_scan_and_watch_delegate(monkeypatch):
    from . import adp_scanner as adp_scanner_mod
    from . import adp_watchlist as adp_watchlist_mod
    from .ingest import get_adapter

    calls: dict[str, object] = {}

    class FakeAdpScanner:
        def __init__(self, cfg, session):
            calls["scan_init"] = (cfg, session)

        def run_once(self):
            return 2

    def fake_watch(cfg, session):
        calls["watch"] = (cfg, session)
        return 1

    monkeypatch.setattr(adp_scanner_mod, "AdpScanner", FakeAdpScanner)
    monkeypatch.setattr(adp_watchlist_mod, "refresh_adp_watchlist", fake_watch)

    adapter = get_adapter("adp_portal")
    assert adapter.scan("CFG", "SES") == 2
    assert calls["scan_init"] == ("CFG", "SES")
    assert adapter.refresh_watchlist("CFG", "SES") == 1
    assert calls["watch"] == ("CFG", "SES")


def test_run_worker_runs_all_scans_before_watches_with_per_adapter_commit(tmp_path, monkeypatch):
    """Loop: scans (por prioridad) → watches, con commit aislado por fuente."""
    from types import SimpleNamespace

    from . import worker as worker_mod

    order: list[str] = []

    def make_adapter(name: str, priority: int):
        return SimpleNamespace(
            source=name,
            scan_priority=priority,
            scan_interval_seconds=lambda cfg: 300,
            watch_interval_seconds=lambda cfg: 600,
            scan=lambda cfg, session, _n=name: (order.append(f"scan:{_n}"), 1)[1],
            refresh_watchlist=lambda cfg, session, _n=name: (
                order.append(f"watch:{_n}"),
                0,
            )[1],
        )

    adapters = [make_adapter("seace", 0), make_adapter("adp_portal", 10)]

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.closed = 0

        def commit(self):
            order.append("commit")
            self.commits += 1

        def rollback(self):
            order.append("rollback")

        def close(self):
            self.closed += 1

    sessions: list[FakeSession] = []

    def fake_session_factory():
        s = FakeSession()
        sessions.append(s)
        return s

    monkeypatch.setattr(worker_mod, "_acquire_worker_lock", lambda data_dir: None)
    monkeypatch.setattr(worker_mod, "init_db", lambda url: None)
    monkeypatch.setattr(worker_mod, "_bootstrap_catalog", lambda s, c: None)
    monkeypatch.setattr(worker_mod, "write_worker_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(worker_mod, "session_factory", fake_session_factory)
    monkeypatch.setattr(worker_mod, "_active_scan_adapters", lambda cfg: adapters)

    cfg = SimpleNamespace(
        data_dir=str(tmp_path),
        database_url="sqlite:///:memory:",
        poll_interval_seconds=300,
        tenant_id="default",
    )

    worker_mod.run_worker(cfg, once=True)

    # Todos los scans (por prioridad) antes que los watches; cada fuente commitea
    # por separado para aislar fallos entre adapters.
    assert order == [
        "scan:seace",
        "commit",
        "scan:adp_portal",
        "commit",
        "watch:seace",
        "commit",
        "watch:adp_portal",
        "commit",
    ]
    loop_session = sessions[-1]  # la última es la del ciclo de trabajo
    assert loop_session.commits == 4
    assert loop_session.closed == 1
    assert all(s.closed == 1 for s in sessions)  # toda sesión se cierra


def test_run_worker_isolates_failure_of_one_adapter(tmp_path, monkeypatch):
    """El fallo del scan de una fuente no descarta el trabajo de otra."""
    from types import SimpleNamespace

    from . import worker as worker_mod

    order: list[str] = []

    def make_adapter(name, priority, *, scan_fails=False):
        def _scan(cfg, session, _n=name, _f=scan_fails):
            order.append(f"scan:{_n}")
            if _f:
                raise RuntimeError("boom")
            return 1

        return SimpleNamespace(
            source=name,
            scan_priority=priority,
            scan_interval_seconds=lambda cfg: 300,
            watch_interval_seconds=lambda cfg: 600,
            scan=_scan,
            refresh_watchlist=lambda cfg, session, _n=name: (
                order.append(f"watch:{_n}"),
                0,
            )[1],
        )

    # SEACE escanea ok; ADP falla. El commit de SEACE debe sobrevivir.
    adapters = [make_adapter("seace", 0), make_adapter("adp_portal", 10, scan_fails=True)]

    class FakeSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0
            self.closed = 0

        def commit(self):
            order.append("commit")
            self.commits += 1

        def rollback(self):
            order.append("rollback")
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    sessions: list[FakeSession] = []

    def fake_session_factory():
        s = FakeSession()
        sessions.append(s)
        return s

    monkeypatch.setattr(worker_mod, "_acquire_worker_lock", lambda data_dir: None)
    monkeypatch.setattr(worker_mod, "init_db", lambda url: None)
    monkeypatch.setattr(worker_mod, "_bootstrap_catalog", lambda s, c: None)
    monkeypatch.setattr(worker_mod, "write_worker_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(worker_mod, "session_factory", fake_session_factory)
    monkeypatch.setattr(worker_mod, "_active_scan_adapters", lambda cfg: adapters)

    cfg = SimpleNamespace(
        data_dir=str(tmp_path),
        database_url="sqlite:///:memory:",
        poll_interval_seconds=300,
        tenant_id="default",
    )

    worker_mod.run_worker(cfg, once=True)

    assert order == [
        "scan:seace",
        "commit",
        "scan:adp_portal",
        "rollback",
        "watch:seace",
        "commit",
        "watch:adp_portal",
        "commit",
    ]
    loop_session = sessions[-1]
    assert loop_session.commits == 3  # seace scan + ambos watches
    assert loop_session.rollbacks == 1  # solo el scan de adp


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
        conn.execute(
            text(
                "CREATE TABLE analysis_results ("
                "id INTEGER PRIMARY KEY, process_id INTEGER NOT NULL UNIQUE, "
                "status VARCHAR(32) DEFAULT 'pending', "
                "FOREIGN KEY(process_id) REFERENCES processes(id))"
            )
        )
        conn.execute(
            text("INSERT INTO analysis_results (id, process_id, status) VALUES (1, 1, 'done')")
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
        analysis_ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='analysis_results'")
        ).scalar()
        assert analysis_ddl is not None
        assert "processes_old" not in analysis_ddl
        conn.execute(text("UPDATE analysis_results SET status = 'error' WHERE id = 1"))
        conn.commit()
