"""Sesión y motor de base de datos."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine = None
_SessionLocal = None

_PROCESS_COLUMN_ADDITIONS = (
    ("source", "VARCHAR(32) DEFAULT 'seace'"),
    ("source_ref", "VARCHAR(256)"),
    ("workflow_profile", "VARCHAR(64) DEFAULT 'public_tender'"),
    ("interest_status", "VARCHAR(32) DEFAULT 'none'"),
    ("nid_convocatoria", "TEXT"),
    ("nid_sistema", "VARCHAR(8)"),
    ("link_id", "VARCHAR(128)"),
    ("ntipo", "VARCHAR(8)"),
    ("ficha_id", "VARCHAR(36)"),
    ("numero", "VARCHAR(16)"),
    ("fecha_publicacion", "VARCHAR(32)"),
    ("reiniciado_desde", "VARCHAR(256)"),
    ("objeto", "VARCHAR(256)"),
    ("descripcion", "TEXT"),
    ("cuantia", "VARCHAR(64)"),
    ("moneda", "VARCHAR(64)"),
    ("version_seace", "VARCHAR(8)"),
    ("fecha_consultas", "VARCHAR(64)"),
    ("fecha_presentacion", "VARCHAR(64)"),
    ("cronograma_json", "TEXT"),
    ("documentos_json", "TEXT"),
    ("ficha_url", "VARCHAR(512)"),
    ("list_hash", "VARCHAR(64)"),
    ("content_hash", "VARCHAR(64)"),
    ("data_dir", "VARCHAR(512)"),
    ("auto_reject_reason", "TEXT"),
    ("watch_unread", "BOOLEAN DEFAULT 0"),
    ("watch_checked_at", "DATETIME"),
    ("watch_cronograma_prev_json", "TEXT"),
    ("watch_documentos_prev_json", "TEXT"),
    ("watch_changelog_json", "TEXT"),
    ("list_rank_descargados", "INTEGER"),
    ("list_rank_analizados", "INTEGER"),
)

_ANALYSIS_COLUMN_ADDITIONS = (("run_id", "VARCHAR(36)"),)

_ENTITY_COLUMN_ADDITIONS = (
    ("estado_osce", "VARCHAR(32)"),
    ("departamento", "VARCHAR(128)"),
    ("provincia", "VARCHAR(128)"),
    ("distrito", "VARCHAR(128)"),
    ("codigo_siaf", "VARCHAR(32)"),
    ("codconsucode", "VARCHAR(32)"),
    ("osce_ultima_actualizacion", "VARCHAR(32)"),
)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


_PG_COLUMN_TYPE = {
    "BOOLEAN DEFAULT 0": "BOOLEAN DEFAULT false",
    "DATETIME": "TIMESTAMP WITH TIME ZONE",
}


def _adapt_column_type(col_type: str, dialect: str) -> str:
    if dialect == "postgresql":
        return _PG_COLUMN_TYPE.get(col_type, col_type)
    return col_type


def _ensure_table_columns(engine, table: str, additions: tuple[tuple[str, str], ...]) -> None:
    """ALTER TABLE ADD COLUMN para despliegues con BD creada antes de nuevas columnas."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    dialect = engine.dialect.name
    existing = {col["name"] for col in insp.get_columns(table)}
    with engine.begin() as conn:
        for name, col_type in additions:
            if name not in existing:
                ddl_type = _adapt_column_type(col_type, dialect)
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def _backfill_process_sources(engine) -> None:
    """Completa columnas multi-ingesta para filas SEACE existentes."""
    insp = inspect(engine)
    if "processes" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("processes")}
    if not {"source", "source_ref", "nid_proceso"}.issubset(existing):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE processes "
                "SET source = 'seace' "
                "WHERE source IS NULL OR source = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE processes "
                "SET source_ref = nid_proceso "
                "WHERE (source_ref IS NULL OR source_ref = '') "
                "AND nid_proceso IS NOT NULL"
            )
        )


def _backfill_process_pipeline_fields(engine) -> None:
    """Completa campos conceptuales de PipelineItem en BDs existentes."""
    insp = inspect(engine)
    if "processes" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("processes")}
    if not {"workflow_profile", "interest_status"}.issubset(existing):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE processes "
                "SET workflow_profile = 'public_tender' "
                "WHERE workflow_profile IS NULL OR workflow_profile = ''"
            )
        )
        conn.execute(
            text(
                "UPDATE processes "
                "SET interest_status = 'none' "
                "WHERE interest_status IS NULL OR interest_status = ''"
            )
        )


def init_db(database_url: str) -> None:
    global _engine, _SessionLocal
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url.startswith("sqlite:///"):
            path = database_url.replace("sqlite:///", "", 1)
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if connect_args:
        engine_kwargs["connect_args"] = connect_args
    _engine = create_engine(database_url, **engine_kwargs)
    if database_url.startswith("sqlite"):
        event.listen(_engine, "connect", _configure_sqlite_connection)

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(_engine)
    _ensure_table_columns(_engine, "entities", _ENTITY_COLUMN_ADDITIONS)
    _ensure_table_columns(_engine, "processes", _PROCESS_COLUMN_ADDITIONS)
    _ensure_table_columns(_engine, "analysis_results", _ANALYSIS_COLUMN_ADDITIONS)
    _backfill_process_sources(_engine)
    _backfill_process_pipeline_fields(_engine)
    if _SessionLocal is not None:
        from ..list_order import backfill_list_ranks

        with _SessionLocal() as session:
            if backfill_list_ranks(session):
                session.commit()
    if _engine.dialect.name == "sqlite":
        _ensure_sqlite_indexes(_engine)


def _ensure_sqlite_indexes(engine) -> None:
    """Índices en BD existentes (create_all no altera tablas ya creadas)."""
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_entities_activa ON entities (activa)",
        "CREATE INDEX IF NOT EXISTS ix_processes_objeto ON processes (objeto)",
        "CREATE INDEX IF NOT EXISTS ix_processes_status_entity ON processes (status, entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_processes_status_objeto ON processes (status, objeto)",
        "CREATE INDEX IF NOT EXISTS ix_processes_source ON processes (source)",
        "CREATE INDEX IF NOT EXISTS ix_processes_source_ref ON processes (source_ref)",
        "CREATE INDEX IF NOT EXISTS ix_processes_workflow_profile ON processes (workflow_profile)",
        "CREATE INDEX IF NOT EXISTS ix_processes_interest_status ON processes (interest_status)",
        "CREATE INDEX IF NOT EXISTS ix_processes_watch_unread ON processes (watch_unread)",
    )
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def session_factory() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("Base de datos no inicializada. Llama a init_db() primero.")
    return _SessionLocal()


def get_session() -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
