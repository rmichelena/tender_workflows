"""Sesión y motor de base de datos."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine = None
_SessionLocal = None


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def init_db(database_url: str) -> None:
    global _engine, _SessionLocal
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url.startswith("sqlite:///"):
            path = database_url.replace("sqlite:///", "", 1)
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args=connect_args or None,
    )
    if database_url.startswith("sqlite"):
        event.listen(_engine, "connect", _configure_sqlite_connection)

    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(_engine)
    if database_url.startswith("sqlite"):
        _ensure_sqlite_indexes(_engine)
        _ensure_entity_columns(_engine)


def _ensure_entity_columns(engine) -> None:
    """Columnas nuevas en entities (create_all no altera tablas existentes)."""
    with engine.begin() as conn:
        cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(entities)")).fetchall()
        }
        additions = (
            ("estado_osce", "VARCHAR(32)"),
            ("departamento", "VARCHAR(128)"),
            ("provincia", "VARCHAR(128)"),
            ("distrito", "VARCHAR(128)"),
            ("codigo_siaf", "VARCHAR(32)"),
            ("codconsucode", "VARCHAR(32)"),
            ("osce_ultima_actualizacion", "VARCHAR(32)"),
        )
        for name, col_type in additions:
            if name not in cols:
                conn.execute(text(f"ALTER TABLE entities ADD COLUMN {name} {col_type}"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_entities_activa ON entities (activa)"
            )
        )


def _ensure_sqlite_indexes(engine) -> None:
    """Índices en BD existentes (create_all no altera tablas ya creadas)."""
    statements = (
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
