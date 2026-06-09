"""Sesión y motor de base de datos."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
import time

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import MetaData

from .models import Base

_engine = None
_SessionLocal = None

_PROCESS_COLUMN_ADDITIONS = (
    ("source", "VARCHAR(32) DEFAULT 'seace'"),
    ("source_ref", "VARCHAR(256)"),
    ("workflow_profile", "VARCHAR(64) DEFAULT 'public_tender'"),
    ("interest_status", "VARCHAR(32) DEFAULT 'none'"),
    ("lifecycle_phase", "VARCHAR(32) DEFAULT 'licitacion'"),
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
    ("auto_reject_exempt", "BOOLEAN DEFAULT 0"),
    ("watch_unread", "BOOLEAN DEFAULT 0"),
    ("watch_checked_at", "DATETIME"),
    ("watch_cronograma_prev_json", "TEXT"),
    ("watch_documentos_prev_json", "TEXT"),
    ("watch_changelog_json", "TEXT"),
    ("list_rank_descargados", "INTEGER"),
    ("list_rank_analizados", "INTEGER"),
    ("promoted_at", "DATETIME"),
)

_ANALYSIS_COLUMN_ADDITIONS = (
    ("run_id", "VARCHAR(36)"),
    ("pipeline_item_id", "INTEGER"),
)

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


def _has_missing_values(engine, table: str, columns: tuple[str, ...]) -> bool:
    predicate = " OR ".join(f"{column} IS NULL OR {column} = ''" for column in columns)
    with engine.connect() as conn:
        row = conn.execute(text(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1")).first()
    return row is not None


def _backfill_process_sources(engine) -> None:
    """Completa columnas multi-ingesta para filas SEACE existentes."""
    insp = inspect(engine)
    if "processes" not in insp.get_table_names():
        return
    existing = {col["name"] for col in insp.get_columns("processes")}
    if not {"source", "source_ref", "nid_proceso"}.issubset(existing):
        return
    with engine.connect() as conn:
        needs_backfill = conn.execute(
            text(
                "SELECT 1 FROM processes "
                "WHERE source IS NULL OR source = '' "
                "OR ((source_ref IS NULL OR source_ref = '') AND nid_proceso IS NOT NULL) "
                "LIMIT 1"
            )
        ).first()
    if needs_backfill is None:
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
    targets = ("workflow_profile", "interest_status")
    if "lifecycle_phase" in existing:
        targets = targets + ("lifecycle_phase",)
    if not _has_missing_values(engine, "processes", targets):
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
        if "lifecycle_phase" in existing:
            conn.execute(
                text(
                    "UPDATE processes "
                    "SET lifecycle_phase = 'licitacion' "
                    "WHERE lifecycle_phase IS NULL OR lifecycle_phase = ''"
                )
            )


def _backfill_tenant_feed_decisions(engine) -> None:
    """Backfill idempotente del overlay desde los campos de autoreject en `processes`.

    Paso 0.3b: copia las decisiones ya materializadas en `Process` al overlay
    `tenant_feed_decisions` (tenant `default`). `exempt` y `autorejected` son disjuntos
    (un proceso eximido no queda en estado autorejected). Reejecutable: salta los que ya
    tienen decisión.
    """
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "tenant_feed_decisions" not in tables or "processes" not in tables:
        return
    with engine.begin() as conn:
        existing = {
            (row[0], row[1])
            for row in conn.execute(
                text("SELECT tenant_id, feed_item_id FROM tenant_feed_decisions")
            )
        }
        rows = conn.execute(
            text(
                "SELECT id, status, auto_reject_reason, auto_reject_exempt "
                "FROM processes WHERE status = 'autorejected' OR auto_reject_exempt = 1"
            )
        ).fetchall()
        to_insert: list[dict] = []
        for pid, status, reason, exempt in rows:
            if ("default", pid) in existing:
                continue
            if exempt:
                to_insert.append(
                    {
                        "tenant_id": "default",
                        "feed_item_id": pid,
                        "decision": "exempt",
                        "rule_id": None,
                        "reason": None,
                    }
                )
            elif status == "autorejected":
                rule_id = reason.split(":", 1)[0].strip() if reason else None
                to_insert.append(
                    {
                        "tenant_id": "default",
                        "feed_item_id": pid,
                        "decision": "autorejected",
                        "rule_id": rule_id,
                        "reason": reason,
                    }
                )
        if to_insert:
            conn.execute(
                text(
                    "INSERT INTO tenant_feed_decisions "
                    "(tenant_id, feed_item_id, decision, rule_id, reason, created_at, updated_at) "
                    "VALUES (:tenant_id, :feed_item_id, :decision, :rule_id, :reason, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                to_insert,
            )


def _purge_orphan_feed_decisions(engine) -> int:
    """Elimina decisiones del overlay que referencian un `Process` inexistente.

    El feed (hoy `processes`) puede borrar items (p. ej. el duplicado que elimina
    `adopt_republication`); como `feed_item_id` no tiene foreign key, esas decisiones
    quedarían huérfanas. Idempotente; se ejecuta en `init_db`.
    """
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "tenant_feed_decisions" not in tables or "processes" not in tables:
        return 0
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM tenant_feed_decisions WHERE feed_item_id NOT IN "
                "(SELECT id FROM processes)"
            )
        )
        return result.rowcount or 0


def _flip_autorejected_status_to_overlay(engine) -> int:
    """Flip one-shot (0.3c-3): saca la decisión de autoreject del `status` del feed.

    Tras mover el autoreject al overlay, el `status=autorejected` legacy queda como
    artefacto: lo devolvemos a `publicada` para que el feed no codifique la decisión del
    tenant. Se flipea cualquier `status=autorejected` que tenga decisión en el overlay,
    sea `autorejected` o `exempt` (un item exento no debe quedar atrapado en
    `status=autorejected`, invisible en la UI). Los que NO tienen respaldo en el overlay
    se conservan (no perder la marca sin red). Las lecturas efectivas no cambian de
    resultado. Idempotente: tras correr no quedan `status=autorejected` con overlay. El
    motivo legacy (`auto_reject_reason`) se conserva como red de display.
    """
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "tenant_feed_decisions" not in tables or "processes" not in tables:
        return 0
    with engine.begin() as conn:
        # Guard: evita correr el UPDATE en cada arranque si ya no queda nada que flipear.
        pending = conn.execute(
            text("SELECT 1 FROM processes WHERE status = 'autorejected' LIMIT 1")
        ).first()
        if pending is None:
            return 0
        result = conn.execute(
            text(
                "UPDATE processes SET status = 'publicada' "
                "WHERE status = 'autorejected' AND id IN ("
                "  SELECT feed_item_id FROM tenant_feed_decisions"
                ")"
            )
        )
        return result.rowcount or 0


def _process_identity_index_names(engine) -> set[str]:
    insp = inspect(engine)
    names: set[str] = set()
    for idx in insp.get_indexes("processes"):
        if idx.get("name"):
            names.add(idx["name"])
    for uc in insp.get_unique_constraints("processes"):
        if uc.get("name"):
            names.add(uc["name"])
    return names


def _sqlite_drop_named_indexes(engine, table_name: str) -> None:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name=:table "
                "AND name NOT LIKE 'sqlite_autoindex_%'"
            ),
            {"table": table_name},
        ).fetchall()
        for (name,) in rows:
            conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))


def _process_migration_copy_defaults() -> dict[str, str]:
    return {
        "source": "'seace'",
        "workflow_profile": "'public_tender'",
        "interest_status": "'none'",
        "lifecycle_phase": "'licitacion'",
        "status": "'publicada'",
        "auto_reject_exempt": "0",
        "watch_unread": "0",
        "first_seen_at": "CURRENT_TIMESTAMP",
        "last_seen_at": "CURRENT_TIMESTAMP",
        "updated_at": "CURRENT_TIMESTAMP",
    }


def _process_migration_select_sql(old_cols: set[str]) -> tuple[str, str]:
    from .models import Process

    copy_defaults = _process_migration_copy_defaults()
    insert_cols: list[str] = []
    select_exprs: list[str] = []
    for col in Process.__table__.columns:
        name = col.name
        insert_cols.append(name)
        if name in old_cols:
            select_exprs.append(name)
        elif name in copy_defaults:
            select_exprs.append(copy_defaults[name])
        elif col.nullable:
            select_exprs.append("NULL")
        else:
            raise RuntimeError(
                f"Migración SQLite: falta valor por defecto para columna {name!r}"
            )
    return ", ".join(insert_cols), ", ".join(select_exprs)


def _sqlite_table_create_sql(engine, table_name: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        ).fetchone()
    return row[0] if row else None


def _sqlite_recreate_table_from_model(engine, model) -> None:
    """Recrea una tabla SQLite conservando filas (resetea FKs rotos)."""
    from .models import Entity, PipelineItem, Process

    table_name = model.__tablename__
    insp = inspect(engine)
    if table_name not in insp.get_table_names():
        return
    old_cols = {col["name"] for col in insp.get_columns(table_name)}
    insert_cols = [col.name for col in model.__table__.columns if col.name in old_cols]
    if not insert_cols:
        return
    cols_sql = ", ".join(insert_cols)
    temp_md = MetaData()
    # Stubs para resolver FKs al compilar CREATE (tablas ya existen en disco).
    Entity.__table__.to_metadata(temp_md)
    Process.__table__.to_metadata(temp_md)
    PipelineItem.__table__.to_metadata(temp_md)
    new_table = model.__table__.to_metadata(temp_md, name=f"{table_name}_new")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text(f"DROP TABLE IF EXISTS {table_name}_new"))
    new_table.create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO {table_name}_new ({cols_sql}) "
                f"SELECT {cols_sql} FROM {table_name}"
            )
        )
        conn.execute(text(f"DROP TABLE {table_name}"))
        conn.execute(text(f"ALTER TABLE {table_name}_new RENAME TO {table_name}"))
        conn.execute(text("PRAGMA foreign_keys=ON"))


def _sqlite_fix_analysis_results_fk_if_needed(engine) -> None:
    """Repara FK hacia processes_old tras rebuild interrumpido."""
    ddl = _sqlite_table_create_sql(engine, "analysis_results")
    if not ddl or "processes_old" not in ddl:
        return
    from .models import AnalysisResult

    _sqlite_recreate_table_from_model(engine, AnalysisResult)


def _sqlite_assert_foreign_key_check(engine) -> None:
    with engine.connect() as conn:
        violations = conn.execute(text("PRAGMA foreign_key_check")).fetchall()
    if violations:
        sample = ", ".join(str(row) for row in violations[:3])
        raise RuntimeError(f"SQLite foreign_key_check falló tras migración: {sample}")


def _sqlite_recover_failed_processes_rebuild(engine) -> bool:
    """Completa un rebuild interrumpido (processes vacía + processes_old con datos)."""
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "processes_old" not in tables or "processes" not in tables:
        return False
    with engine.connect() as conn:
        old_count = conn.execute(text("SELECT COUNT(*) FROM processes_old")).scalar()
        cur_count = conn.execute(text("SELECT COUNT(*) FROM processes")).scalar()
    if old_count == 0 or cur_count > 0:
        return False

    old_cols = {col["name"] for col in insp.get_columns("processes_old")}
    cols_sql, select_sql = _process_migration_select_sql(old_cols)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(
            text(f"INSERT INTO processes ({cols_sql}) SELECT {select_sql} FROM processes_old")
        )
        conn.execute(text("DROP TABLE processes_old"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
    _sqlite_fix_analysis_results_fk_if_needed(engine)
    _sqlite_assert_foreign_key_check(engine)
    return True


def _sqlite_rebuild_processes_table(engine) -> None:
    """Recrea `processes` con el esquema actual (nid_proceso nullable, identidad por source)."""
    from .models import Entity, Process

    _ensure_table_columns(engine, "processes", _PROCESS_COLUMN_ADDITIONS)
    _backfill_process_pipeline_fields(engine)

    insp = inspect(engine)
    old_cols = {col["name"] for col in insp.get_columns("processes")}
    cols_sql, select_sql = _process_migration_select_sql(old_cols)
    temp_md = MetaData()
    # Stub de `entities` en el MetaData temporal: SQLAlchemy necesita resolver la FK al
    # compilar el CREATE de `processes_new` (la tabla ya existe en disco).
    Entity.__table__.to_metadata(temp_md)
    processes_new = Process.__table__.to_metadata(temp_md, name="processes_new")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(text("DROP TABLE IF EXISTS processes_new"))
    processes_new.create(engine)
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO processes_new ({cols_sql}) SELECT {select_sql} FROM processes")
        )
        conn.execute(text("DROP TABLE processes"))
        conn.execute(text("ALTER TABLE processes_new RENAME TO processes"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
    _sqlite_fix_analysis_results_fk_if_needed(engine)
    _sqlite_assert_foreign_key_check(engine)


def _has_process_source_identity_unique(engine) -> bool:
    insp = inspect(engine)
    target = {"source", "entity_id", "source_ref"}
    for uc in insp.get_unique_constraints("processes"):
        if set(uc.get("column_names") or ()) == target:
            return True
    return "uq_process_source_identity" in _process_identity_index_names(engine)


def _migrate_process_identity_schema(engine) -> None:
    """Alinea identidad multi-ingesta en BDs existentes."""
    insp = inspect(engine)
    if "processes" not in insp.get_table_names():
        return

    _backfill_process_sources(engine)

    if engine.dialect.name == "sqlite" and _sqlite_recover_failed_processes_rebuild(engine):
        insp = inspect(engine)

    dialect = engine.dialect.name
    cols = {col["name"]: col for col in insp.get_columns("processes")}
    nid_col = cols.get("nid_proceso")
    if nid_col and nid_col.get("nullable") is False:
        if dialect == "postgresql":
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE processes ALTER COLUMN nid_proceso DROP NOT NULL"))
        elif dialect == "sqlite":
            _sqlite_rebuild_processes_table(engine)
            insp = inspect(engine)

    if not _has_process_source_identity_unique(engine):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_process_source_identity "
                    "ON processes (source, entity_id, source_ref)"
                )
            )


# Estados que implican trabajo curado (promoción feed→pipeline, 0.3d).
_PROMOTED_STATUSES = (
    "descargando",
    "descargada",
    "descartando",
    "analizada",
    "portafolio",
    "archivando",
    "archivada",
)


def _backfill_promoted_at(engine) -> int:
    """Backfill idempotente de `promoted_at` (0.3d): marca como promovidos los items con
    trabajo curado histórico (status de pipeline, interés marcado, descarga o análisis).

    Solo toca filas con `promoted_at IS NULL` que cumplen la condición de promoción, de
    modo que es seguro correr en cada arranque. Usa `first_seen_at` como timestamp.
    """
    insp = inspect(engine)
    if "processes" not in insp.get_table_names():
        return 0
    cols = {col["name"] for col in insp.get_columns("processes")}
    if "promoted_at" not in cols:
        return 0
    has_analysis = "analysis_results" in insp.get_table_names()
    status_list = ", ".join(f"'{s}'" for s in _PROMOTED_STATUSES)
    conds = [
        f"status IN ({status_list})",
        "(interest_status IS NOT NULL AND interest_status NOT IN ('none', ''))",
        "(data_dir IS NOT NULL AND data_dir != '')",
    ]
    if has_analysis:
        conds.append("id IN (SELECT process_id FROM analysis_results)")
    predicate = " OR ".join(conds)
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE processes SET promoted_at = first_seen_at "
                f"WHERE promoted_at IS NULL AND ({predicate})"
            )
        )
        return result.rowcount or 0


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
    # Register dual-write hook as before_commit event (covers ALL commit paths)
    # before_commit runs after flush (PKs assigned) and triggers an auto-flush
    # for any new objects we add (the PipelineItems)
    # event removed — dual-write is handled in commit helpers
    Base.metadata.create_all(_engine)
    _ensure_table_columns(_engine, "entities", _ENTITY_COLUMN_ADDITIONS)
    _ensure_table_columns(_engine, "processes", _PROCESS_COLUMN_ADDITIONS)
    _ensure_table_columns(_engine, "analysis_results", _ANALYSIS_COLUMN_ADDITIONS)
    _backfill_process_pipeline_fields(_engine)
    _migrate_process_identity_schema(_engine)
    _backfill_tenant_feed_decisions(_engine)
    _purge_orphan_feed_decisions(_engine)
    _flip_autorejected_status_to_overlay(_engine)
    _backfill_promoted_at(_engine)
    if _SessionLocal is not None:
        from ..list_order import backfill_list_ranks

        with _SessionLocal() as session:
            if backfill_list_ranks(session):
                session.commit()
    if _engine.dialect.name == "sqlite":
        _ensure_sqlite_indexes(_engine)
    _backfill_pipeline_items(_engine)


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
        "CREATE INDEX IF NOT EXISTS ix_processes_lifecycle_phase ON processes (lifecycle_phase)",
        "CREATE INDEX IF NOT EXISTS ix_processes_auto_reject_exempt ON processes (auto_reject_exempt)",
        "CREATE INDEX IF NOT EXISTS ix_processes_watch_unread ON processes (watch_unread)",
        "CREATE INDEX IF NOT EXISTS ix_processes_promoted_at ON processes (promoted_at)",
    )
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _backfill_pipeline_items(engine) -> None:
    """Migración one-shot (0.3e-1): copia promoted rows de processes → pipeline_items.

    Idempotente: solo inserta filas que no existen (por origin_feed_id). También
    backfillea `analysis_results.pipeline_item_id` para los items que ya tienen análisis.
    No toca la tabla `processes` — el dual-write viene en 0.3e-2.
    """
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "pipeline_items" not in tables or "processes" not in tables:
        return

    # Columnas a copiar (excluimos id, promoted_at que tiene default).
    # source/source_ref van como origin_source/origin_source_ref (no como columnas directas).
    copy_cols = [
        "entity_id", "anio", "status",
        "workflow_profile", "interest_status", "lifecycle_phase",
        "nid_proceso", "nid_convocatoria", "nid_sistema", "link_id",
        "ntipo", "ficha_id", "numero", "fecha_publicacion",
        "nomenclatura", "reiniciado_desde", "objeto", "descripcion",
        "cuantia", "moneda", "version_seace",
        "fecha_consultas", "fecha_presentacion", "cronograma_json",
        "documentos_json", "ficha_url",
        "content_hash", "data_dir",
        "watch_unread", "watch_checked_at",
        "watch_cronograma_prev_json", "watch_documentos_prev_json",
        "watch_changelog_json",
        "list_rank_descargados", "list_rank_analizados",
        "first_seen_at", "updated_at", "promoted_at",
    ]
    # Columnas leídas de processes (incluye source/source_ref para el mapeo)
    select_cols = ["source", "source_ref"] + copy_cols

    # Ensure unique index on origin_feed_id (review finding: idempotency guard)
    pi_cols_set = {col["name"] for col in insp.get_columns("pipeline_items")}
    if "origin_feed_id" in pi_cols_set:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_origin_feed_id "
                "ON pipeline_items (origin_feed_id)"
            ))

    # Ensure index on analysis_results.pipeline_item_id (review finding: ADD COLUMN no FK)
    if "analysis_results" in tables:
        ar_cols_set = {col["name"] for col in insp.get_columns("analysis_results")}
        if "pipeline_item_id" in ar_cols_set:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_analysis_results_pipeline_item_id "
                    "ON analysis_results (pipeline_item_id)"
                ))

    # Verificar que processes tiene promoted_at
    proc_cols = {col["name"] for col in insp.get_columns("processes")}
    if "promoted_at" not in proc_cols:
        return

    # Solo copiar promoted (promoted_at IS NOT NULL) que no existan ya
    with engine.begin() as conn:
        existing = {
            row[0] for row in conn.execute(
                text("SELECT origin_feed_id FROM pipeline_items WHERE origin_feed_id IS NOT NULL")
            )
        }

        rows = conn.execute(
            text(
                "SELECT id, " + ", ".join(select_cols) + " "
                "FROM processes WHERE promoted_at IS NOT NULL"
            )
        ).fetchall()

        if not rows:
            return

        pi_cols = ["origin_feed_id", "origin_source", "origin_source_ref", "tenant_id"] + copy_cols
        if engine.dialect.name == "sqlite":
            insert_sql = (
                "INSERT OR IGNORE INTO pipeline_items ("
                + ", ".join(pi_cols)
                + ") VALUES ("
                + ", ".join(f":{c}" for c in pi_cols)
                + ")"
            )
        else:
            insert_sql = (
                "INSERT INTO pipeline_items ("
                + ", ".join(pi_cols)
                + ") SELECT "
                + ", ".join(f":{c}" for c in pi_cols)
                + " WHERE NOT EXISTS (SELECT 1 FROM pipeline_items WHERE origin_feed_id = :origin_feed_id)"
            )

        # row layout: [0]=id, [1]=source, [2]=source_ref, [3:]=copy_cols
        for row in rows:
            feed_id = row[0]
            if feed_id in existing:
                continue
            vals = {"origin_feed_id": feed_id, "tenant_id": "default"}
            vals["origin_source"] = row[1] or "seace"
            vals["origin_source_ref"] = row[2]
            for i, col in enumerate(copy_cols):
                vals[col] = row[3 + i]
            conn.execute(text(insert_sql), vals)

    # Backfill analysis_results.pipeline_item_id
    if "analysis_results" in tables:
        ar_cols = {col["name"] for col in insp.get_columns("analysis_results")}
        if "pipeline_item_id" in ar_cols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE analysis_results SET pipeline_item_id = "
                        "(SELECT pi.id FROM pipeline_items pi WHERE pi.origin_feed_id = analysis_results.process_id) "
                        "WHERE pipeline_item_id IS NULL AND process_id IS NOT NULL"
                    )
                )


def _sync_dirty_promoted(session: Session) -> None:
    """Dual-write (0.3e-2): sincroniza Process promovidos a PipelineItem.

    Se llama antes del commit. Captura objetos sucios/nuevos ANTES de flush,
    flush para obtener PKs, luego sincroniza y flush de nuevo.
    """
    from .models import Process as _Process, AnalysisResult as _AR, PipelineItem as _PI
    from .pipeline_sync import sync_to_pipeline

    # Capture candidates BEFORE flush (flush moves them to persistent)
    candidates = [
        obj for obj in list(session.dirty) + list(session.new)
        if isinstance(obj, (_Process, _AR))
    ]
    if not candidates:
        return

    # Flush to ensure all PKs are assigned
    session.flush()

    synced_ids: set[int] = set()
    needs_flush = False

    for obj in candidates:
        if isinstance(obj, _Process) and obj.promoted_at is not None and obj.id is not None:
            sync_to_pipeline(session, obj)
            synced_ids.add(obj.id)
            needs_flush = True
        elif isinstance(obj, _AR):
            if obj.process_id is not None:
                proc = session.get(_Process, obj.process_id)
                if proc is not None and proc.promoted_at is not None and proc.id not in synced_ids:
                    sync_to_pipeline(session, proc)
                    synced_ids.add(proc.id)
                    needs_flush = True
                if obj.pipeline_item_id is None and obj.process_id is not None:
                    pi = (
                        session.query(_PI)
                        .filter(_PI.origin_feed_id == obj.process_id)
                        .one_or_none()
                    )
                    if pi is not None:
                        obj.pipeline_item_id = pi.id
                        needs_flush = True

    if needs_flush:
        session.flush()


def commit_session_with_retry(
    session: Session, *, attempts: int = 8, delay: float = 0.25
) -> None:
    """Commit con reintentos ante bloqueos transitorios de SQLite.

    Antes del commit, sincroniza los Process promovidos sucios a PipelineItem
    (dual-write 0.3e-2). Solo procesa objetos que están en la sesión (new/dirty).
    """
    _sync_dirty_promoted(session)
    for attempt in range(attempts):
        try:
            session.commit()
            return
        except OperationalError as exc:
            orig = getattr(exc, "orig", exc)
            message = str(orig).lower()
            if "locked" not in message and "busy" not in message:
                raise
            session.rollback()
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def session_factory() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("Base de datos no inicializada. Llama a init_db() primero.")
    session = _SessionLocal()
    # Wrap commit to ensure dual-write sync (covers ALL commit paths)
    _original_commit = session.commit
    def _commit_with_sync():
        _sync_dirty_promoted(session)
        _original_commit()
    session.commit = _commit_with_sync  # type: ignore[assignment]
    return session


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
