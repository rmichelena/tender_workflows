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


def _ensure_table_columns(engine, table: str, additions: tuple[tuple[str, str], ...]) -> None:
    """ALTER TABLE ADD COLUMN para despliegues con BD creada antes de nuevas columnas."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns(table)}
    with engine.begin() as conn:
        for name, col_type in additions:
            if name not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}"))


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
    if _engine.dialect.name == "sqlite":
        _ensure_sqlite_indexes(_engine)


def _ensure_sqlite_indexes(engine) -> None:
    """Índices en BD existentes (create_all no altera tablas ya creadas)."""
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_entities_activa ON entities (activa)",
        "CREATE INDEX IF NOT EXISTS ix_processes_objeto ON processes (objeto)",
        "CREATE INDEX IF NOT EXISTS ix_processes_status_entity ON processes (status, entity_id)",
        "CREATE INDEX IF NOT EXISTS ix_processes_status_objeto ON processes (status, objeto)",
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
